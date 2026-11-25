"""HTTP + SSE do `gaia serve`.

Este modulo nao conhece o agente: fala com um `Target` (ver protocolo abaixo).
O transporte e proposital e minimalista — stdlib, sem dependencia nova:

* ``GET /events``  SSE com ``id:`` (seq) e ``event:`` (tipo); ``?since=`` ou
  ``Last-Event-ID`` retomam de onde o cliente parou.
* ``POST /turn``            ``{"text": "..."}`` -> ``202 {"run": "..."}``
* ``POST /permission/{id}`` ``{"decision": "once|always|reject"}``
* ``POST /abort``
* ``GET  /health``, ``GET /sessions``, ``GET /models``, ``GET /search``

Leitura do que a TUI ja faz (paridade no web):

* ``GET  /engine``   modelo, provedor, chave mascarada, modo, ocupado
* ``GET  /modes``, ``POST /mode``
* ``GET  /history``, ``POST /session/open``, ``POST /session/new``
* ``GET  /commands``, ``POST /chinese`` (eco 中文 do catalogo de comandos)
* ``GET  /languages``, ``POST /language`` (dialog "idiomas" do web: zh, en, fr)
* ``GET  /automations``
* ``GET  /instances`` (maquinas do ``instances.json``, com status)

Credencial: ``Authorization: Bearer <GAIA_SERVE_TOKEN>`` **ou** o nonce de
sessao no cookie ``gaia_chat`` (``POST /session`` troca um pelo outro). O
``Bearer`` continua valendo para `gaia --remote` e `curl`.

O `RemoteUI` e a fonte dos eventos: cada assinante tem sua fila, e o handler
apenas serializa. Sem estado compartilhado alem do `RemoteUI`.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from gaia.bots import BotIndisponivel
from gaia.control import routines as routines_mod
from gaia.controller import command_catalog as commands
from gaia.interface.remote import RemoteUI
from gaia.serve import control_api, session, web
from gaia.serve import instances as instances_mod
from gaia.serve.session import ControlSessions, Sessions
from gaia.serve.voice import VoiceUnavailable, clamp_window

logger = logging.getLogger(__name__)

# Teto do upload de voz (WAV de ate 3 minutos ~5,5 MB). Acima disso a rota
# responde 413 sem entregar bytes ao STT.
UPLOAD_MAX_BYTES = 8 * 1024 * 1024

DECISIONS = {"once", "always", "reject"}
# Idiomas do eco conhecidos pelo transporte. O motor real responde pela lista
# completa (`Target.languages`); isto e o minimo para um alvo antigo/sem
# `languages()` nao deixar o botao "idiomas" sem nada.
IDIOMAS_BASICOS: dict[str, str] = {"zh": "中文", "en": "English", "fr": "Français"}
HEARTBEAT_SECONDS = 15.0
CONTROL_PREFIX = "/control"
STATIC_PREFIX = "/static"
class Target(Protocol):
    """O que o transporte precisa saber do motor. `gaia/serve/app.py` liga nisso."""

    def health(self) -> dict[str, Any]: ...

    def sessions(self) -> list[dict[str, Any]]: ...

    def models(self) -> list[dict[str, Any]]:
        """Modelos disponiveis (lista vazia = nenhum provedor com chave)."""

    def switch_model(self, slug: str) -> list[str]:
        """Troca o modelo e devolve as mensagens do /modelo."""

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Busca no Second Brain. Lista vazia = nada encontrado."""

    def turn(self, text: str) -> str:
        """Agenda um turno e devolve o id do run (nao espera terminar)."""

    def decide(self, request_id: str, decision: str) -> None: ...

    def abort(self) -> None: ...

    def bots(self) -> list[dict[str, Any]]:
        """Bots do registry (id, nome, papel, disponivel, atual). Opcional."""

    def select_bot(self, key: str) -> list[str]:
        """Troca o bot ativo. Opcional: alvo antigo responde 503."""

    def activity(self, limit: int = 50, bot: str = "") -> dict[str, Any]:
        """Feed de atividade dos bots (mensagens + runs). Opcional."""

    def mark_seen(self, bot: str = "") -> dict[str, Any]:
        """Zera o nao-lido do feed (``bot`` vazio = todos). Opcional."""


def _opcional(target: Any, nome: str) -> Any:
    """Metodo opcional do `Target` (rota que so existe em motor de verdade).

    Os fakes dos testes implementam so o basico: sem o metodo a rota responde
    503 ("nao disponivel neste motor") em vez de estourar AttributeError.
    """
    funcao = getattr(target, nome, None)
    return funcao if callable(funcao) else None

def _aguardar(handler: Any, resultado: Any) -> Any:
    """Espera um awaitable rodando no loop do motor, sem bloquear o HTTP.

    Mesmo padrao do ``control_api._aguardar``: o socket vive na thread do
    servidor, o awaitable tem de rodar no loop do ``Orchestrator``.
    """
    if not hasattr(resultado, "__await__"):
        return resultado
    esperar = getattr(handler.app.target, "run_awaitable", None)
    if esperar is None:
        raise RuntimeError("motor sem runner para operacoes assincronas")
    return esperar(resultado)


class ServeConfigError(ValueError):
    """Configuracao que exporia o servidor (bind na rede sem token)."""


def _eh_loopback(host: str) -> bool:
    return host in {"", "127.0.0.1", "localhost", "::1"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "gaia-serve"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- utilidades
    @property
    def app(self) -> ServeServer:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # silencio no teste
        logger.debug("serve: " + fmt, *args)

    def _json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        cabecalhos: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for nome, valor in (cabecalhos or {}).items():
            self.send_header(nome, valor)
        self.end_headers()
        self.wfile.write(body)

    def _erro(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"error": code, "message": message})

    def _bearer(self) -> str:
        header = self.headers.get("Authorization", "")
        prefixo = "Bearer "
        return header[len(prefixo) :].strip() if header.startswith(prefixo) else ""

    def _autorizado(self) -> bool:
        """Credencial do chat: ``Bearer`` ou cookie de sessao (``gaia_chat``).

        Sem ``GAIA_SERVE_TOKEN`` configurado nao ha o que proteger (loopback):
        tudo passa, como sempre foi.
        """
        token = self.app.token
        if not token:
            return True
        if session.segredo_igual(self._bearer(), token):
            return True
        return self.app.chat_sessions.valid(self.chat_session)

    @property
    def chat_session(self) -> str:
        """Nonce do cookie do chat (vazio se nao veio cookie)."""
        return session.cookie_value(
            self.headers.get("Cookie", ""), session.CHAT_COOKIE_NAME
        )

    @property
    def control_session(self) -> str:
        """Nonce do cookie do painel (vazio se nao veio cookie)."""
        return session.cookie_value(self.headers.get("Cookie", ""), session.COOKIE_NAME)

    def _autorizado_controle(self) -> bool:
        """Token do painel e SEPARADO do token do chat (separacao de poderes).

        O token de chat nao da poder administrativo: ele so conversa. Se
        ``GAIA_CONTROL_TOKEN`` nao estiver definido, a API do painel nao existe
        (503) — nunca herda o ``serve.token`` por omissao.

        Duas provas valem, ambas derivadas do MESMO segredo: o ``Bearer`` (que
        o cliente fino e o ``curl`` usam) e o nonce de sessao emitido depois de
        um ``Bearer`` correto (o cookie que o navegador guarda).
        """
        esperado = self.app.control_token
        if not esperado:
            return False
        if session.segredo_igual(self._bearer(), esperado):
            return True
        return self.app.control_sessions.valid(self.control_session)

    def _trocar_token_por_sessao(self) -> bool:
        """POST /session: ``Bearer`` do chat correto -> cookie HttpOnly.

        So um ``Bearer`` valido cria sessao: o cookie nunca e emitido de graca
        (senao qualquer site com ``SameSite=None`` ganharia acesso de leitura).
        Sem ``GAIA_SERVE_TOKEN`` configurado, a sessao nao muda nada (o servidor
        ja e aberto no loopback) e a rota responde ok sem cookie.
        """
        esperado = self.app.token
        if not esperado:
            self._json(
                HTTPStatus.OK,
                {"ok": True, "authenticated": True, "session": False},
            )
            return True
        token = self._bearer()
        if not session.segredo_igual(token, esperado):
            self._erro(HTTPStatus.UNAUTHORIZED, "unauthorized", "token invalido")
            return False
        nonce = self.app.chat_sessions.grant()
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "authenticated": True,
                "session": True,
                "expires_in": session.SESSION_TTL_SECONDS,
            },
            {"Set-Cookie": session.chat_set_cookie(nonce)},
        )
        return True

    def _sair_da_sessao_do_chat(self) -> None:
        """DELETE /session: apaga o nonce do chat. Idempotente."""
        self.app.chat_sessions.revoke(self.chat_session)
        self._json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": session.chat_clear_cookie()},
        )

    def _trocar_token_do_painel(self) -> bool:
        """POST /control/api/session: ``Bearer`` do painel -> cookie do painel."""
        esperado = self.app.control_token
        if not esperado:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no_control_token",
                "control center desativado: defina GAIA_CONTROL_TOKEN",
            )
            return False
        token = self._bearer()
        if not session.segredo_igual(token, esperado):
            self._erro(
                HTTPStatus.UNAUTHORIZED, "unauthorized", "token do painel invalido"
            )
            return False
        nonce = self.app.control_sessions.grant()
        self._json(
            HTTPStatus.OK,
            {"ok": True, "expires_in": session.SESSION_TTL_SECONDS},
            {"Set-Cookie": session.set_cookie(nonce)},
        )
        return True

    def _sair_da_sessao(self) -> None:
        """DELETE /control/api/session: apaga o nonce e o cookie.

        Funciona mesmo sem sessao valida (idempotente): o objetivo e esquecer.
        """
        nonce = self.control_session
        self.app.control_sessions.revoke(nonce)
        self._json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": session.clear_cookie()},
        )

    def _corpo(self) -> dict[str, Any] | None:
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho <= 0:
            return {}
        if tamanho > 1_000_000:
            return None
        try:
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
        except ValueError:
            return None
        return dados if isinstance(dados, dict) else None

    def _corpo_bytes(self, limite: int) -> bytes | None:
        """Corpo cru (audio do navegador). ``None`` = grande demais.

        O body precisa ser sempre drenado: sem ler, o `Content-Length` fica
        pendente no socket e a proxima requisicao da mesma conexao le lixo.
        """
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho <= 0:
            return b""
        if tamanho > limite:
            # Le so o comeco para nao travar o socket; o resto e descartado
            # quando a conexao fecha (Connection: close na resposta de erro).
            self.rfile.read(min(tamanho, limite))
            self.close_connection = True
            return None
        return self.rfile.read(tamanho)

    def _host(self) -> str:
        return str(self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "")

    # ------------------------------------------------------------------- rotas
    def do_GET(self) -> None:
        url = urlparse(self.path)
        rota = url.path.rstrip("/") or "/"
        if rota == "/health":
            # Uma falha aqui não pode virar "empty reply": o cliente precisa
            # de JSON para saber que o motor está quebrado (e o comando
            # `gmin serve status` depende disso).
            try:
                saude = self.app.target.health()
            except Exception as exc:
                logger.warning("health falhou: %s", exc, exc_info=True)
                self._erro(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "health_failed", str(exc)
                )
                return
            self._json(HTTPStatus.OK, {"ok": True, **saude})
            return
        if rota == "/session":
            self._json(
                HTTPStatus.OK,
                {
                    "authenticated": self._autorizado(),
                    "required": bool(self.app.token),
                    "mode": "session" if self.chat_session else "bearer",
                },
            )
            return
        if rota == "/instances":
            # Publica de proposito: a tela pergunta antes de autenticar, e a
            # resposta nao tem token nenhum (nem mascarado).
            self._instancias()
            return
        if rota == "/events":
            if not self._autorizado():
                self._erro(HTTPStatus.UNAUTHORIZED, "unauthorized", "token invalido")
                return
            self._eventos(parse_qs(url.query))
            return
        if rota == "/":
            # Pagina estatica: publica como /health. Sem token ela nao faz nada
            # (todo o resto exige Authorization) — quem pede o token e a tela.
            self._arquivo(web.page(), web.content_type())
            return
        if rota == "/favicon.ico":
            # O navegador pede isto sozinho na raiz do site, sem token.
            corpo, tipo = web.icon()
            self._arquivo(corpo, tipo)
            return
        if rota.startswith(f"{STATIC_PREFIX}/"):
            self._asset(rota[len(STATIC_PREFIX) + 1 :])
            return
        if rota == CONTROL_PREFIX or rota == f"{CONTROL_PREFIX}/":
            self._pagina_controle()
            return
        if rota.startswith(f"{CONTROL_PREFIX}/static/"):
            self._asset_controle(rota[len(f"{CONTROL_PREFIX}/static/") :])
            return
        if rota.startswith(f"{CONTROL_PREFIX}/api/"):
            control_api.get(self, rota, parse_qs(url.query))
            return
        if not self._autorizado():
            self._erro(HTTPStatus.UNAUTHORIZED, "unauthorized", "token invalido")
            return
        if rota == "/engine":
            self._json(HTTPStatus.OK, self._engine())
            return
        if rota == "/backlog":
            # Card do backlog: paginas "backlog" da Agenda + itens do chat, com
            # o estado do despacho ao pi. Leitura pura (o banco e a verdade).
            self._backlog()
            return
        if rota == "/automations":
            # Agenda + estado da proatividade para a tela de automacoes do chat.
            # O token exigido e o do chat: pausar uma rotina e operacao, nao
            # administracao de credencial (o painel continua dono de /control).
            self._automacoes()
            return
        if rota == "/providers":
            # Mesmo JSON de `/control/api/providers` (chaves sempre mascaradas).
            self._provedores()
            return
        if rota == "/bots":
            # Bots do registry: a mesma lista que `/modes` devolve por
            # compatibilidade, com o estado de disponibilidade da maquina, o
            # badge de nao-lido e o indicador de rodando (rail do web).
            self._json(HTTPStatus.OK, {"bots": self._modes()})
            return
        if rota == "/bots/activity":
            # Feed do painel: mensagens e execucoes recentes por bot. Leitura
            # pura; 503 quando o motor nao expoe atividade (fake/alvo antigo).
            self._atividade(parse_qs(url.query))
            return
        if rota == "/modes":
            self._json(HTTPStatus.OK, {"modes": self._modes()})
            return
        if rota == "/languages":
            # Idiomas de eco que a tela pode oferecer (zh, en, fr). A tela monta
            # o dialog "idiomas" daqui em vez de ter um botao 中文 fixo.
            self._json(
                HTTPStatus.OK,
                {"languages": self._idiomas(), "language": self._idioma_atual()},
            )
            return
        if rota == "/commands":
            # Catalogo unico (`controller/command_catalog.py`): a paleta do web
            # filtra por `names`; o despacho continua em `/turn`.
            self._json(HTTPStatus.OK, {"commands": commands.as_dicts()})
            return
        if rota == "/voice":
            # O modo voz do web precisa de microfone e STT nesta maquina; a tela
            # pergunta antes de mostrar o botao.
            self._json(HTTPStatus.OK, self._voz_status())
            return
        if rota == "/history":
            self._json(HTTPStatus.OK, {"history": self._history()})
            return
        if rota == "/search":
            query = (parse_qs(url.query).get("q") or [""])[0].strip()
            try:
                limite = int((parse_qs(url.query).get("limit") or ["8"])[0])
            except ValueError:
                limite = 8
            limite = max(1, min(limite, 50))
            hits = self.app.target.search(query, limite) if query else []
            self._json(HTTPStatus.OK, {"query": query, "hits": hits})
            return
        if rota == "/sessions":
            self._json(HTTPStatus.OK, {"sessions": self.app.target.sessions()})
            return
        if rota == "/models":
            try:
                modelos = self.app.target.models()
            except Exception as exc:  # lista indisponivel nao derruba o servidor
                logger.warning("lista de modelos falhou: %s", exc)
                self._erro(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "models_failed", str(exc)
                )
                return
            self._json(HTTPStatus.OK, {"models": modelos})
            return
        self._erro(HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        rota = url.path.rstrip("/") or "/"
        if rota == "/control/api/session":
            self._corpo()  # drena o body (POST pode vir com {}) e mantem o socket vivo
            self._trocar_token_do_painel()
            return
        if rota.startswith(f"{CONTROL_PREFIX}/api/"):
            corpo = self._corpo()
            if corpo is None:
                self._erro(HTTPStatus.BAD_REQUEST, "bad_json", "corpo JSON invalido")
                return
            control_api.post(self, rota, corpo)
            return
        if rota == "/session":
            self._corpo()
            self._trocar_token_por_sessao()
            return
        if not self._autorizado():
            self._erro(HTTPStatus.UNAUTHORIZED, "unauthorized", "token invalido")
            return
        if rota == "/voice/upload":
            # Audio cru (audio/wav): o body NAO e JSON, entao a rota responde
            # antes do `_corpo()` comum.
            self._upload_voz()
            return
        corpo = self._corpo()
        if corpo is None:
            self._erro(HTTPStatus.BAD_REQUEST, "bad_json", "corpo JSON invalido")
            return
        if rota == "/turn":
            texto = str(corpo.get("text") or "").strip()
            if not texto:
                self._erro(HTTPStatus.BAD_REQUEST, "empty_text", "texto vazio")
                return
            try:
                run = self.app.target.turn(texto)
            except RuntimeError as exc:  # turno ja em andamento
                self._erro(HTTPStatus.CONFLICT, "busy", str(exc))
                return
            self._json(HTTPStatus.ACCEPTED, {"run": run})
            return
        if rota == "/abort":
            self.app.target.abort()
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if rota == "/voice/listen":
            self._escutar(corpo)
            return
        if rota == "/voice/stop":
            # Idempotente: solta a janela de escuta (botao desligado no meio).
            parar = _opcional(self.app.target, "stop_listening")
            if parar is not None:
                parar()
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if rota == "/voice/speak":
            self._falar(corpo)
            return
        if rota == "/backlog/sync":
            # Botao "Atualizar": consulta a Agenda AGORA (pode levar segundos).
            self._backlog_sync()
            return
        if rota == "/backlog/add":
            # Item novo pelo chat/voz (o card tem o campo; o /backlog add tambem).
            self._backlog_add(corpo)
            return
        if rota == "/backlog/run":
            # Botao "Executar": enfileira o item; o worker executa e avisa.
            self._backlog_run(corpo)
            return
        if rota == "/backlog/repo":
            # Projeto sem repositorio: salva o mapa no TOML e destrava o
            # despacho. Escrita em config passa pelo ControlCenter (backup).
            self._backlog_repo(corpo)
            return
        if rota == "/automations/set":
            # Checkbox da tela de automacoes: {id, enabled}. Separada de
            # /automations (GET) para nao misturar leitura e escrita na mesma
            # rota e para o 404 de id desconhecido ser explicito.
            self._mudar_automacao(corpo)
            return
        if rota == "/automations/run":
            # "rodar agora" (tela de automacoes): executa a rotina de verdade,
            # com trigger=manual. Pode demorar (rede) e avisa no Telegram — por
            # isso a tela confirma antes e mostra "rodando...".
            self._rodar_automacao(corpo)
            return
        if rota == "/proactive":
            # Checkbox da proatividade: grava no TOML e aplica no processo na
            # hora (o `/proativo on|off` da TUI faz o mesmo sem gravar).
            self._proatividade(corpo)
            return
        if rota == "/chinese":
            # Liga/desliga o eco 中文 do web. Nao grava no TOML: e estado do
            # processo, igual ao `/chines` da TUI.
            ligado = bool(corpo.get("enabled", True))
            try:
                ativo = self._chines(ligado)
            except Exception as exc:
                logger.warning("eco em chines falhou: %s", exc)
                self._erro(HTTPStatus.BAD_REQUEST, "chinese_failed", str(exc))
                return
            self._json(HTTPStatus.OK, {"ok": True, "chinese": ativo})
            return
        if rota == "/language":
            # Dialog "idiomas": {key: "zh"|"en"|"fr", enabled?: bool}. Sem
            # `enabled` so escolhe o idioma (liga o eco). A resposta traz a
            # lista para a tela repintar sem um segundo GET.
            chave = str(corpo.get("key") or corpo.get("language") or "").strip()
            if not chave:
                self._erro(HTTPStatus.BAD_REQUEST, "empty_language", "idioma vazio")
                return
            ativar = corpo.get("enabled")
            try:
                self._idioma(chave, None if ativar is None else bool(ativar))
            except ValueError as exc:
                self._erro(HTTPStatus.BAD_REQUEST, "bad_language", str(exc))
                return
            except Exception as exc:
                logger.warning("troca de idioma falhou: %s", exc)
                self._erro(HTTPStatus.BAD_REQUEST, "language_failed", str(exc))
                return
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "key": chave.lower(),
                    "chinese": bool(self._chines_ativo()),
                    "language": self._idioma_atual(chave),
                    "languages": self._idiomas(),
                },
            )
            return
        if rota == "/model":
            slug = str(corpo.get("model") or "").strip()
            if not slug:
                self._erro(HTTPStatus.BAD_REQUEST, "empty_model", "modelo vazio")
                return
            try:
                mensagens = self.app.target.switch_model(slug)
            except RuntimeError as exc:  # turno em andamento
                self._erro(HTTPStatus.CONFLICT, "busy", str(exc))
                return
            except Exception as exc:
                logger.warning("troca de modelo falhou: %s", exc)
                self._erro(
                    HTTPStatus.BAD_REQUEST, "model_failed", str(exc)
                )
                return
            self._json(
                HTTPStatus.OK,
                {"ok": True, "model": slug, "messages": mensagens},
            )
            return
        if rota == "/bots/seen":
            # Badge do rail: {bot?: "ariana"}; vazio zera todos.
            chave = str(corpo.get("bot") or corpo.get("id") or "").strip()
            try:
                resultado = self._marcar_visto(chave)
            except RuntimeError as exc:
                self._erro(
                    HTTPStatus.SERVICE_UNAVAILABLE, "activity_unavailable", str(exc)
                )
                return
            except Exception as exc:
                logger.warning("marcar visto falhou: %s", exc)
                self._erro(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "seen_failed", str(exc)
                )
                return
            self._json(HTTPStatus.OK, resultado)
            return
        if rota == "/bot/select":
            chave = str(corpo.get("id") or corpo.get("bot") or corpo.get("mode") or "").strip()
            if not chave:
                self._erro(HTTPStatus.BAD_REQUEST, "empty_bot", "bot vazio")
                return
            try:
                mensagens = self._selecionar_bot(chave)
            except BotIndisponivel as exc:
                # Configuracao certa, dependencia do ambiente faltando (ex.:
                # clone da dissertacao): 409 com motivo, nunca queda no padrao.
                self._erro(HTTPStatus.CONFLICT, "bot_unavailable", str(exc))
                return
            except RuntimeError as exc:
                self._erro(HTTPStatus.CONFLICT, "busy", str(exc))
                return
            except ValueError as exc:
                # Id desconhecido nao cai no padrao em silencio.
                self._erro(HTTPStatus.BAD_REQUEST, "bot_failed", str(exc))
                return
            except Exception as exc:
                logger.warning("troca de bot falhou: %s", exc)
                self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "bot_failed", str(exc))
                return
            atual = next((b for b in self._modes() if b.get("current")), {})
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "bot": atual.get("id") or chave,
                    "messages": mensagens,
                    "engine": self._engine(),
                    "history": self._history(),
                    "sessions": self._sessions(),
                    "bots": self._modes(),
                },
            )
            return
        if rota == "/mode":
            chave = str(corpo.get("mode") or "").strip()
            if not chave:
                self._erro(HTTPStatus.BAD_REQUEST, "empty_mode", "modo vazio")
                return
            try:
                mensagens = self._selecionar_modo(chave)
            except RuntimeError as exc:
                self._erro(HTTPStatus.CONFLICT, "busy", str(exc))
                return
            except ValueError as exc:
                self._erro(HTTPStatus.BAD_REQUEST, "mode_failed", str(exc))
                return
            except Exception as exc:
                logger.warning("troca de modo falhou: %s", exc)
                self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "mode_failed", str(exc))
                return
            self._json(
                HTTPStatus.OK,
                {"ok": True, "mode": chave, "messages": mensagens, "engine": self._engine()},
            )
            return
        if rota in {"/session/open", "/session/new"}:
            try:
                if rota == "/session/new":
                    mensagens = self._nova_sessao()
                else:
                    try:
                        identificador = int(corpo.get("id") or 0)
                    except (TypeError, ValueError):
                        self._erro(
                            HTTPStatus.BAD_REQUEST, "bad_id", "informe o id da sessao"
                        )
                        return
                    if identificador <= 0:
                        self._erro(
                            HTTPStatus.BAD_REQUEST, "bad_id", "informe o id da sessao"
                        )
                        return
                    mensagens = self._abrir_sessao(identificador)
            except RuntimeError as exc:
                self._erro(HTTPStatus.CONFLICT, "busy", str(exc))
                return
            except Exception as exc:
                logger.warning("troca de sessao falhou: %s", exc)
                self._erro(HTTPStatus.BAD_REQUEST, "session_failed", str(exc))
                return
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "messages": mensagens,
                    "engine": self._engine(),
                    "history": self._history(),
                },
            )
            return
        if rota.startswith("/permission/"):
            request_id = rota[len("/permission/") :]
            decisao = str(corpo.get("decision") or "")
            if not request_id:
                self._erro(HTTPStatus.BAD_REQUEST, "missing_id", "request_id vazio")
                return
            if decisao not in DECISIONS:
                self._erro(
                    HTTPStatus.BAD_REQUEST,
                    "bad_decision",
                    "decision deve ser once, always ou reject",
                )
                return
            # Quem espera é o `RemoteUI` (a thread do turno está bloqueada nele),
            # então o transporte destrava primeiro e avisa o alvo depois, para
            # ele registrar/encaminhar se quiser.
            resolvido = self.app.ui.resolve_permission(request_id, decisao)
            self.app.target.decide(request_id, decisao)
            self._json(HTTPStatus.OK, {"ok": True, "resolved": resolvido})
            return
        self._erro(HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}")

    # --------------------------------------------------- dados do motor/estado
    def _engine(self) -> dict[str, Any]:
        """Cabeçalho do web: instancia, modelo, provedor, chave mascarada, modo."""
        dados: dict[str, Any] = {}
        try:
            dados = dict(self.app.target.health() or {})
        except Exception as exc:  # motor quebrado ainda responde algo
            logger.warning("engine: health falhou: %s", exc)
            dados = {"error": str(exc)}
        centro = self.app.center
        provedor: dict[str, Any] = {}
        if centro is not None:
            try:
                info = centro.providers()
                atual: dict[str, Any] = next(
                    (p for p in info.get("providers", []) if p.get("current")), {}
                )
                provedor = {
                    "provider": atual.get("name") or info.get("agent", {}).get("provider", ""),
                    "masked": atual.get("masked", ""),
                    "has_key": bool(atual.get("has_key")),
                    "auth_source": atual.get("auth_source", ""),
                    "local": bool(atual.get("local")),
                }
            except Exception as exc:
                logger.warning("engine: providers falhou: %s", exc)
        instancias = self.app.instances_current(host_header=self._host())
        ativa = next((i for i in instancias if i.get("current")), None)
        return {
            "instance": ativa or self.app.instance_identity(),
            "instances": instancias,
            "model": dados.get("model", ""),
            "provider": provedor.get("provider", ""),
            "masked": provedor.get("masked", ""),
            "has_key": provedor.get("has_key", False),
            "auth_source": provedor.get("auth_source", ""),
            "mode": dados.get("mode", ""),
            # Bot ativo (id) e nome completo: `mode`/`mode_key` continuam
            # sendo a chave historica, que agora e o proprio id do bot.
            "bot": dados.get("bot", dados.get("mode_key", "")),
            "bot_name": dados.get("mode", ""),
            "busy": bool(dados.get("busy")),
            "chinese": bool(dados.get("chinese")),
            "language": self._idioma_atual(),
            # Lista dos idiomas do eco junto do resto do cabecalho: o botao
            # "idiomas" so pode aparecer se a tela souber que existe idioma
            # (buscar em `GET /languages` so ao clicar era ovo e galinha).
            "languages": self._idiomas(),
            "session": dados.get("session", ""),
            "version": dados.get("version", ""),
            "control_enabled": bool(self.app.control_token),
        }

    def _modes(self) -> list[dict[str, Any]]:
        """Lista de bots do motor.

        `/bots` e `/modes` (e `POST /bot/select`/`POST /mode`) leem a MESMA
        fonte: `Target.bots` quando existe, senao o antigo `Target.modes`.
        Assim um alvo novo nao precisa duplicar a regra, e um alvo antigo
        continua funcionando.
        """
        funcao = getattr(self.app.target, "bots", None)
        if callable(funcao):
            return list(funcao())
        antigo = getattr(self.app.target, "modes", None)
        if not callable(antigo):
            return []
        return list(antigo())

    def _selecionar_modo(self, chave: str) -> list[str]:
        funcao = getattr(self.app.target, "select_mode", None)
        if not callable(funcao):
            raise RuntimeError("troca de modo indisponivel neste motor")
        return list(funcao(chave))

    def _selecionar_bot(self, chave: str) -> list[str]:
        """`POST /bot/select`: cai no alias antigo se o motor nao tiver bots."""
        funcao = getattr(self.app.target, "select_bot", None)
        if callable(funcao):
            return list(funcao(chave))
        return self._selecionar_modo(chave)

    def _atividade(self, query: dict[str, list[str]]) -> None:
        """GET /bots/activity: feed do painel (mensagens + runs)."""
        funcao = _opcional(self.app.target, "activity")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "activity_unavailable",
                "atividade dos bots indisponivel neste motor",
            )
            return
        try:
            limite = int((query.get("limit") or ["30"])[0])
        except (TypeError, ValueError):
            limite = 30
        bot = str((query.get("bot") or [""])[0]).strip()
        try:
            dados = funcao(limite, bot)
        except Exception as exc:
            logger.warning("atividade dos bots falhou: %s", exc)
            self._erro(
                HTTPStatus.INTERNAL_SERVER_ERROR, "activity_failed", str(exc)
            )
            return
        self._json(HTTPStatus.OK, dados)

    def _marcar_visto(self, bot: str) -> dict[str, Any]:
        """`POST /bots/seen`: delega ao motor; 503 quando ele nao tem feed."""
        funcao = _opcional(self.app.target, "mark_seen")
        if funcao is None:
            raise RuntimeError("feed dos bots indisponivel neste motor")
        return dict(funcao(bot) or {})

    def _sessions(self) -> list[dict[str, Any]]:
        """Conversas do bot ativo (o cliente redesenha ao trocar de bot)."""
        funcao = getattr(self.app.target, "sessions", None)
        if not callable(funcao):
            return []
        return list(funcao())

    def _abrir_sessao(self, identificador: int) -> list[str]:
        funcao = getattr(self.app.target, "open_session", None)
        if not callable(funcao):
            raise RuntimeError("troca de sessao indisponivel neste motor")
        return list(funcao(identificador))

    def _nova_sessao(self) -> list[str]:
        funcao = getattr(self.app.target, "new_session", None)
        if not callable(funcao):
            raise RuntimeError("nova sessao indisponivel neste motor")
        return list(funcao())

    def _chines(self, ligado: bool) -> bool:
        funcao = getattr(self.app.target, "set_chinese", None)
        if not callable(funcao):
            raise RuntimeError("eco em chines indisponivel neste motor")
        return bool(funcao(ligado))

    def _idiomas(self) -> list[dict[str, Any]]:
        """Lista de idiomas do motor; motor antigo cai nos tres do basico."""
        funcao = getattr(self.app.target, "languages", None)
        if callable(funcao):
            return list(funcao())
        atual = self._idioma_atual()
        return [
            {
                "key": chave,
                "label": rotulo,
                "code": chave,
                "current": chave == atual,
                "enabled": bool(self._chines_ativo()),
            }
            for chave, rotulo in IDIOMAS_BASICOS.items()
        ]

    def _idioma_atual(self, chave: str | None = None) -> str:
        """Idioma do eco em uso: o do alvo quando existe, senao o chines."""
        ler = getattr(self.app.target, "echo_language", None)
        escolhido = ler() if callable(ler) else None
        codigo = str(chave or escolhido or "zh").strip().lower()
        return codigo if codigo in IDIOMAS_BASICOS else "zh"

    def _idioma(self, chave: str, ligado: bool | None) -> bool:
        trocar = getattr(self.app.target, "set_language", None)
        if callable(trocar):
            return bool(trocar(chave, ligado))
        # Motor antigo: so existe o chines.
        if chave.lower() != "zh":
            raise ValueError(f"idioma desconhecido: {chave}")
        return self._chines(True if ligado is None else ligado)

    def _chines_ativo(self) -> bool:
        """Estado do eco 中文 sem exigir que o alvo tenha `chinese()`."""
        ler = getattr(self.app.target, "chinese", None)
        if callable(ler):
            return bool(ler())
        saude = self.app.target.health()
        return bool((saude or {}).get("chinese"))

    def _history(self) -> list[dict[str, Any]]:
        funcao = getattr(self.app.target, "history", None)
        if not callable(funcao):
            return []
        return list(funcao())

    def _automacoes(self) -> None:
        centro = self.app.center
        if centro is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no_center",
                "automações exigem GAIA_CONTROL_TOKEN configurado"
                if not self.app.control_token
                else "automações indisponíveis: este motor subiu sem Control Center",
            )
            return
        try:
            self._json(HTTPStatus.OK, centro.automations())
        except Exception as exc:
            logger.warning("automations falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "automations_failed", str(exc))

    # ------------------------------------------------------------------ backlog
    def _backlog(self) -> None:
        """GET /backlog: grupos por projeto, contadores e estado do worker."""
        funcao = _opcional(self.app.target, "backlog_snapshot")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "backlog_unavailable",
                "backlog indisponivel neste motor",
            )
            return
        try:
            self._json(HTTPStatus.OK, funcao())
        except Exception as exc:
            logger.warning("backlog falhou: %s", exc, exc_info=True)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "backlog_failed", str(exc))

    def _backlog_sync(self) -> None:
        """POST /backlog/sync: le as paginas da Agenda e grava os itens."""
        funcao = _opcional(self.app.target, "backlog_sync")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "backlog_unavailable",
                "backlog indisponivel neste motor",
            )
            return
        try:
            resultado = _aguardar(self, funcao())
        except RuntimeError as exc:
            # Backlog desligado ou motor sem loop: a pessoa corrige config.
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "backlog_disabled", str(exc))
            return
        except Exception as exc:
            # Agenda fora do ar/credencial: a tela mostra o motivo real.
            logger.warning("backlog sync falhou: %s", exc)
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "agenda_failed", str(exc))
            return
        self._json(HTTPStatus.OK, resultado)

    def _backlog_add(self, corpo: dict[str, Any]) -> None:
        """POST /backlog/add: item novo pelo chat/voz."""
        funcao = _opcional(self.app.target, "backlog_add")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "backlog_unavailable",
                "backlog indisponivel neste motor",
            )
            return
        texto = str(corpo.get("text") or "").strip()
        if not texto:
            self._erro(HTTPStatus.BAD_REQUEST, "empty_text", "texto vazio")
            return
        projeto = str(corpo.get("project") or "").strip()
        try:
            item = funcao(texto, projeto)
        except ValueError as exc:
            self._erro(HTTPStatus.BAD_REQUEST, "bad_item", str(exc))
            return
        except RuntimeError as exc:
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "backlog_disabled", str(exc))
            return
        except Exception as exc:
            logger.warning("backlog add falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "backlog_failed", str(exc))
            return
        self._json(HTTPStatus.OK, {"ok": True, "item": item})

    def _backlog_repo(self, corpo: dict[str, Any]) -> None:
        """POST /backlog/repo: grava ``[backlog.repos].<projeto>`` no TOML.

        O card pede o repo do projeto novo (pagina ``x backlog`` sem mapeamento)
        no proprio formulario de item. Sem a rota, o grupo apareceria e o
        despacho recusaria para sempre.
        """
        funcao = _opcional(self.app.target, "backlog_set_repo")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "backlog_unavailable",
                "backlog indisponivel neste motor",
            )
            return
        projeto = str(corpo.get("project") or "").strip()
        if not projeto:
            self._erro(HTTPStatus.BAD_REQUEST, "missing_project", "informe o projeto")
            return
        repo = str(corpo.get("repo") or "").strip()
        if not repo:
            self._erro(HTTPStatus.BAD_REQUEST, "missing_repo", "informe dono/repo")
            return
        try:
            resultado = funcao(projeto, repo)
        except ValueError as exc:
            self._erro(HTTPStatus.BAD_REQUEST, "bad_repo", str(exc))
            return
        except RuntimeError as exc:
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "backlog_disabled", str(exc))
            return
        except Exception as exc:
            logger.warning("backlog repo falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "backlog_failed", str(exc))
            return
        self._json(HTTPStatus.OK, {"ok": True, **resultado})

    def _backlog_run(self, corpo: dict[str, Any]) -> None:
        """POST /backlog/run: enfileira o item para o mini/pi."""
        funcao = _opcional(self.app.target, "backlog_enqueue")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "backlog_unavailable",
                "backlog indisponivel neste motor",
            )
            return
        bruto = corpo.get("id")
        try:
            if bruto is None:
                raise TypeError("id ausente")
            item_id = int(bruto)
        except (TypeError, ValueError):
            self._erro(HTTPStatus.BAD_REQUEST, "missing_id", "informe o id do item")
            return
        try:
            item = funcao(item_id)
        except KeyError:
            self._erro(
                HTTPStatus.NOT_FOUND, "unknown_item", f"item de backlog desconhecido: {item_id}"
            )
            return
        except RuntimeError as exc:
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "backlog_disabled", str(exc))
            return
        except Exception as exc:
            logger.warning("backlog run falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "backlog_failed", str(exc))
            return
        self._json(HTTPStatus.ACCEPTED, {"ok": True, "item": item})

    def _mudar_automacao(self, corpo: dict[str, Any]) -> None:
        """Liga/desliga uma automacao (tabela `automations`) ou rotina (`routines`).

        Os ids sao disjuntos (`briefing:*`/`career:*`/`memory:daily`/
        `memory:dream` vs `initiative:*`/`memory:previous_day`/...), entao o
        despacho e por lookup no registry. A tela usa a MESMA rota para as duas
        familias: e o mesmo conceito (o que a Gaia faz sozinha), so com driver
        diferente (janela de horario vs gate de config/evento).
        """
        centro = self.app.center
        if centro is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no_center",
                "automações exigem GAIA_CONTROL_TOKEN configurado",
            )
            return
        automation_id = str(corpo.get("id") or "").strip()
        if not automation_id:
            self._erro(HTTPStatus.BAD_REQUEST, "missing_id", "informe id")
            return
        corpo_sem_id = {chave: valor for chave, valor in corpo.items() if chave != "id"}
        if not corpo_sem_id:
            self._erro(
                HTTPStatus.BAD_REQUEST, "missing_value", "informe enabled (ou outro campo)"
            )
            return
        try:
            if routines_mod.find(automation_id) is not None:
                atual = centro.update_routine(automation_id, corpo_sem_id)
                self._json(HTTPStatus.OK, {"ok": True, "routine": atual})
                return
            self._json(
                HTTPStatus.OK,
                {"ok": True, "automation": centro.update_automation(automation_id, corpo_sem_id)},
            )
        except KeyError:
            self._erro(HTTPStatus.NOT_FOUND, "unknown_automation", f"automação desconhecida: {automation_id}")
        except ValueError as exc:
            # Inclui id desconhecido sem tabela semeada e payload invalido: a
            # mensagem ja diz o que corrigir.
            self._erro(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_automation", str(exc))
        except Exception as exc:
            logger.warning("mudar automacao falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "automation_failed", str(exc))

    def _rodar_automacao(self, corpo: dict[str, Any]) -> None:
        """Executa a automacao (ou rotina) agora e devolve o run registrado.

        404 = id desconhecido (ou rotina sem handler), 409 = motor ocupado com
        um turno, 503 = motor sem Control Center. A espera roda no loop do motor
        (``run_awaitable``), porque a rotina faz rede e pode levar minutos.
        """
        centro = self.app.center
        if centro is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no_center",
                "automações exigem GAIA_CONTROL_TOKEN configurado",
            )
            return
        automation_id = str(corpo.get("id") or "").strip()
        if not automation_id:
            self._erro(HTTPStatus.BAD_REQUEST, "missing_id", "informe id")
            return
        automacao_rotina = routines_mod.find(automation_id) is not None
        try:
            if automacao_rotina:
                resultado = _aguardar(self, centro.run_routine(automation_id))
            else:
                resultado = _aguardar(self, centro.run_automation(automation_id))
        except KeyError:
            self._erro(
                HTTPStatus.NOT_FOUND,
                "unknown_automation",
                f"automação desconhecida: {automation_id}",
            )
            return
        except RuntimeError as exc:
            # Motor ocupado (turno em andamento) ou motor sem o metodo: nos dois
            # casos a acao certa e tentar depois, nao corrigir payload.
            self._erro(HTTPStatus.CONFLICT, "busy", str(exc))
            return
        except Exception as exc:
            logger.warning("rodar automacao falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "automation_failed", str(exc))
            return
        self._json(HTTPStatus.OK, {"ok": True, "run": resultado})

    def _proatividade(self, corpo: dict[str, Any]) -> None:
        """Liga/desliga a proatividade no TOML e no processo."""
        centro = self.app.center
        if centro is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no_center",
                "proatividade editável exige GAIA_CONTROL_TOKEN configurado",
            )
            return
        if "enabled" not in corpo:
            self._erro(HTTPStatus.BAD_REQUEST, "missing_value", "informe enabled")
            return
        ligado = corpo.get("enabled")
        if not isinstance(ligado, bool):
            self._erro(
                HTTPStatus.BAD_REQUEST, "bad_enabled", "enabled deve ser true ou false"
            )
            return
        try:
            resultado = _aguardar(self, centro.set_proactive(ligado))
        except ValueError as exc:
            # Valor vem do .env: a tela mostra o motivo em vez de mentir.
            self._erro(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_setting", str(exc))
            return
        except Exception as exc:
            logger.warning("proatividade falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "proactive_failed", str(exc))
            return
        self._json(HTTPStatus.OK, {"ok": True, "proactive": resultado})

    def _provedores(self) -> None:
        centro = self.app.center
        if centro is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no_center",
                "provedores exigem GAIA_CONTROL_TOKEN configurado",
            )
            return
        try:
            self._json(HTTPStatus.OK, centro.providers())
        except Exception as exc:
            logger.warning("providers falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "providers_failed", str(exc))

    def _instancias(self) -> None:
        """GET /instances: lista publica com status medido em paralelo."""
        try:
            self._json(
                HTTPStatus.OK,
                {
                    "instances": self.app.instances_current(
                        probe=True, host_header=self._host()
                    )
                },
            )
        except Exception as exc:  # sonda nunca derruba a rota
            logger.warning("instances falhou: %s", exc)
            self._json(HTTPStatus.OK, {"instances": []})

    # ------------------------------------------------------------------- voz
    def _voz_status(self) -> dict[str, Any]:
        funcao = _opcional(self.app.target, "voice_status")
        if funcao is None:
            return {
                "available": False,
                "reason": "modo voz indisponivel neste motor",
                "tts": False,
                "state": "idle",
                "source": "browser",
                "microphone": False,
            }
        try:
            dados = dict(funcao() or {})
        except Exception as exc:  # microfone quebrado nao derruba o header
            logger.warning("voice_status falhou: %s", exc)
            return {
                "available": False,
                "reason": str(exc),
                "tts": False,
                "state": "idle",
                "source": "browser",
                "microphone": False,
            }
        dados.setdefault("available", False)
        dados.setdefault("reason", "")
        dados.setdefault("tts", False)
        dados.setdefault("state", "idle")
        dados.setdefault("source", "browser")
        dados.setdefault("microphone", False)
        return dados

    def _escutar(self, corpo: dict[str, Any]) -> None:
        """POST /voice/listen: grava uma fala no motor e devolve o texto."""
        funcao = _opcional(self.app.target, "listen")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "voice_unavailable",
                "modo voz indisponivel neste motor",
            )
            return
        try:
            janela = clamp_window(corpo.get("window", 15.0))
        except (TypeError, ValueError):  # pragma: no cover - clamp_window normaliza
            janela = 15.0
        try:
            dados = funcao(janela)
        except VoiceUnavailable as exc:
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "voice_unavailable", str(exc))
            return
        except RuntimeError as exc:
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "voice_unavailable", str(exc))
            return
        except Exception as exc:  # microfone/STT nunca vira connection reset
            logger.warning("escuta do web falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "voice_failed", str(exc))
            return
        texto = str((dados or {}).get("text") or "").strip()
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "text": texto,
                "heard": bool((dados or {}).get("heard", bool(texto))),
                "window": janela,
            },
        )

    def _upload_voz(self) -> None:
        """POST /voice/upload: WAV do microfone do navegador -> texto.

        Mesmo JSON de ``/voice/listen`` (a tela so troca a origem do audio).
        WAV invalido/longo demais e 400, nunca 500: o erro e do pedido, nao do
        motor. Nada e gravado em disco.
        """
        funcao = _opcional(self.app.target, "upload_voice")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "voice_unavailable",
                "modo voz indisponivel neste motor",
            )
            return
        corpo = self._corpo_bytes(UPLOAD_MAX_BYTES)
        if corpo is None:
            self._erro(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "audio_too_large",
                f"audio acima de {UPLOAD_MAX_BYTES // (1024 * 1024)} MB",
            )
            return
        if not corpo:
            self._erro(HTTPStatus.BAD_REQUEST, "empty_audio", "audio vazio")
            return
        try:
            dados = funcao(corpo)
        except VoiceUnavailable as exc:
            self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "voice_unavailable", str(exc))
            return
        except ValueError as exc:  # WAV invalido ou longo demais
            self._erro(HTTPStatus.BAD_REQUEST, "invalid_audio", str(exc))
            return
        except Exception as exc:  # STT quebrado nao vira connection reset
            logger.warning("upload de voz falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "voice_failed", str(exc))
            return
        texto = str((dados or {}).get("text") or "").strip()
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "text": texto,
                "heard": bool((dados or {}).get("heard", bool(texto))),
                "window": 0,
            },
        )

    def _falar(self, corpo: dict[str, Any]) -> None:
        """POST /voice/speak: audio (base64) da resposta para o `<audio>`."""
        funcao = _opcional(self.app.target, "speak")
        if funcao is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "voice_unavailable",
                "modo voz indisponivel neste motor",
            )
            return
        texto = str(corpo.get("text") or "").strip()
        if not texto:
            self._erro(HTTPStatus.BAD_REQUEST, "empty_text", "texto vazio")
            return
        try:
            dados = funcao(texto)
        except Exception as exc:
            logger.warning("fala do web falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "voice_failed", str(exc))
            return
        audio = str((dados or {}).get("audio") or "")
        self._json(
            HTTPStatus.OK,
            {"ok": True, "audio": audio, "mime": str((dados or {}).get("mime") or "")},
        )

    # ------------------------------------------------------- paginas e assets
    def _pagina_controle(self) -> None:
        """HTML do painel. Se nao ha token configurado, a tela explica o porque.

        A pagina e publica de proposito (ela pede o token), mas sem
        ``GAIA_CONTROL_TOKEN`` ela nao passa de uma instrucao: nenhuma rota de
        API existe, entao nao ha o que administrar.

        Com o painel ligado, a pagina ja emite a troca do token pela sessao: o
        ``control.js`` nao guarda a credencial em ``localStorage`` e o modal de
        token so aparece quando nao ha cookie valido.
        """
        if not self.app.control_token:
            corpo = (
                b"<!doctype html><html lang=pt-BR><meta charset=utf-8>"
                b"<link rel=icon href=/static/icon.png>"
                b"<title>Control Center desativado</title>"
                b"<body style='font:15px system-ui;padding:2rem;max-width:60ch'>"
                b"<h1>Control Center desativado</h1>"
                b"<p>Defina <code>GAIA_CONTROL_TOKEN</code> no "
                b"<code>~/.config/gaia/.env</code> e reinicie o "
                b"<code>gaia serve</code>.</p>"
                b"<p>O token do chat (<code>GAIA_SERVE_TOKEN</code>) nao vale "
                b"aqui: o painel administra credenciais e configuracao.</p>"
                b"</body></html>"
            )
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(corpo)
            return
        self._arquivo(
            web.control_page(),
            "text/html; charset=utf-8",
            {"X-Content-Type-Options": "nosniff"},
        )

    def _asset(self, nome: str) -> None:
        """Assets do chat (``/static/shared.js`` etc.). Allowlist explicita."""
        achado = web.static_asset(nome)
        if achado is None:
            self._erro(HTTPStatus.NOT_FOUND, "not_found", f"asset: {nome}")
            return
        corpo, tipo = achado
        self._arquivo(corpo, tipo)

    def _asset_controle(self, nome: str) -> None:
        achado = web.control_asset(nome)
        if achado is None:
            self._erro(HTTPStatus.NOT_FOUND, "not_found", f"asset: {nome}")
            return
        corpo, tipo = achado
        self._arquivo(corpo, tipo)

    def _arquivo(
        self, corpo: bytes, tipo: str, cabecalhos: dict[str, str] | None = None
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-cache")
        if not cabecalhos or "X-Content-Type-Options" not in cabecalhos:
            self.send_header("X-Content-Type-Options", "nosniff")
        for nome, valor in (cabecalhos or {}).items():
            self.send_header(nome, valor)
        self.end_headers()
        self.wfile.write(corpo)

    # --------------------------------------------------------------------- SSE
    def do_PUT(self) -> None:
        url = urlparse(self.path)
        rota = url.path.rstrip("/") or "/"
        if not rota.startswith(f"{CONTROL_PREFIX}/api/"):
            self._erro(HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}")
            return
        corpo = self._corpo()
        if corpo is None:
            self._erro(HTTPStatus.BAD_REQUEST, "bad_json", "corpo JSON invalido")
            return
        control_api.post(self, rota, corpo)

    def do_PATCH(self) -> None:
        self.do_PUT()

    def do_DELETE(self) -> None:
        url = urlparse(self.path)
        rota = url.path.rstrip("/") or "/"
        if rota == "/control/api/session":
            self._sair_da_sessao()
            return
        if rota == "/session":
            self._sair_da_sessao_do_chat()
            return
        if not rota.startswith(f"{CONTROL_PREFIX}/api/"):
            self._erro(HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}")
            return
        control_api.delete(self, rota)

    def _eventos(self, query: dict[str, list[str]]) -> None:
        since: int | None = None
        brutos = query.get("since") or []
        if brutos:
            try:
                since = int(brutos[0])
            except ValueError:
                since = None
        if since is None:
            cabecalho = self.headers.get("Last-Event-ID")
            if cabecalho:
                try:
                    since = int(cabecalho)
                except ValueError:
                    since = None
        fila = self.app.ui.subscribe(since=since)
        # Assinar ANTES dos cabecalhos: o cliente desbloqueia no header e pode
        # emitir imediatamente; assinar depois perderia esse evento.
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while not self.app.parar.is_set():
                try:
                    evento = fila.get(timeout=HEARTBEAT_SECONDS)
                except Exception:  # Empty: mantem a conexao viva
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._escrever(evento)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.app.ui.unsubscribe(fila)

    def _escrever(self, evento: dict[str, Any]) -> None:
        dados = json.dumps(evento, ensure_ascii=False)
        bloco = (
            f"id: {evento.get('seq', 0)}\n"
            f"event: {evento.get('type', 'message')}\n"
            f"data: {dados}\n\n"
        )
        self.wfile.write(bloco.encode("utf-8"))
        self.wfile.flush()


class ServeServer:
    """Dono do socket. `start()` sobe em thread e devolve a URL base."""

    def __init__(
        self,
        target: Target,
        ui: RemoteUI,
        *,
        host: str = "127.0.0.1",
        port: int = 8787,
        token: str = "",
        control_token: str = "",
        center: Any = None,
        instance_id: str = "",
        instances_path: Any = None,
        probe: Callable[[Any, float], dict[str, Any]] | None = None,
    ) -> None:
        if not _eh_loopback(host) and not token.strip():
            raise ServeConfigError(
                f"recusando escutar em {host} sem token: defina GAIA_SERVE_TOKEN"
            )
        self.target = target
        self.ui = ui
        self.token = token.strip()
        # Segredo separado do token do chat: sem ele, /control/api nao existe.
        self.control_token = control_token.strip()
        # Sessoes de cookie: painel (``gaia_control``) e chat (``gaia_chat``).
        # Conjuntos separados de proposito: o nonce de um nao vale no outro.
        self.control_sessions = ControlSessions()
        self.chat_sessions = Sessions()
        self.center = center if self.control_token else None
        # `instances.json` e lido por requisicao: editar o arquivo e o jeito de
        # adicionar uma maquina, sem reiniciar o serve.
        self.instance_id = instance_id.strip()
        self.instances_path = instances_path
        self.probe = probe
        self.parar = threading.Event()
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.app = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ ciclo
    def serve_forever(self) -> None:
        self._httpd.serve_forever(poll_interval=0.2)

    def start(self) -> str:
        self._thread = threading.Thread(
            target=self.serve_forever, name="gaia-serve", daemon=True
        )
        self._thread.start()
        return self.base_url

    def shutdown(self) -> None:
        self.parar.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ dados
    @property
    def address(self) -> tuple[str, int]:
        host, porta = self._httpd.server_address[:2]
        return str(host), int(porta)

    @property
    def base_url(self) -> str:
        host, porta = self.address
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        return f"http://{host}:{porta}"

    # -------------------------------------------------------------- instancias
    def instance_identity(self) -> dict[str, Any]:
        """Esta instancia, quando ela nao esta (ou nao casa) no arquivo."""
        return {
            "id": self.instance_id or "local",
            "name": "esta instância",
            "url": self.base_url,
            "current": True,
        }

    def instances_current(
        self, *, probe: bool = False, host_header: str = ""
    ) -> list[dict[str, Any]]:
        """Instancias do arquivo, ``current`` marcado e status opcional."""
        host = host_header or f"127.0.0.1:{self.address[1]}"
        try:
            itens = instances_mod.load(self.instances_path)
        except Exception as exc:  # arquivo nunca derruba a rota
            logger.warning("instances.json: %s", exc)
            itens = []
        itens = instances_mod.marcar_atual(itens, host, self.instance_id)
        if not probe:
            return [item.public() for item in itens]
        sonda = self.probe or instances_mod.probe
        return instances_mod.describe(itens, probe_fn=sonda)


__all__ = ["ServeConfigError", "ServeServer", "Target"]
