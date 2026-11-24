"""O motor por trás do transporte: liga `Orchestrator` ao `Target` do serve.

Aqui (e só aqui) o transporte conhece o agente. O `Orchestrator` roda num loop
asyncio numa thread; o servidor HTTP roda em outra. A ponte é
``asyncio.run_coroutine_threadsafe`` — assíncrono de um lado, thread do outro.

Quem responde aprovação é o `RemoteUI`: o pedido chega como evento `permission`
e fica esperando (numa thread) até ``POST /permission/{id}`` destravar.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import inspect
import logging
import secrets
import threading
from typing import Any

from gaia.config import ECHO_LABELS, ECHO_LANGUAGES
from gaia.interface.remote import RemoteUI, _plain
from gaia.serve.voice import (
    LISTEN_TIMEOUT_SLACK,
    WebVoice,
    clamp_window,
)

logger = logging.getLogger(__name__)


# Idiomas do eco: o idioma de cada traducao (chave da tela -> (rotulo, codigo)).
# O eco so traduz quando `interface.chinese_echo` esta ligado; o idioma vem de
# `interface.echo_language` (zh | en | fr), o mesmo do comando `/chines` da TUI.
IDIOMAS_ECO: dict[str, tuple[str, str]] = {
    chave: (ECHO_LABELS[chave], chave) for chave in ECHO_LANGUAGES
}


def idioma_eco(escolhido: str | None = None) -> str:
    """Idioma de eco em uso (cai no chines quando a chave nao e conhecida)."""
    chave = str(escolhido or "zh").strip().lower()
    return chave if chave in IDIOMAS_ECO else "zh"


class MotorOcupado(RuntimeError):
    """Já existe um turno em andamento; o transporte traduz em HTTP 409."""


class OrchestratorTarget:
    """`Target` do `gaia serve`, sobre um `Orchestrator` real."""

    def __init__(self, orchestrator: Any, ui: RemoteUI) -> None:
        self.orch = orchestrator
        self.ui = ui
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tarefa: concurrent.futures.Future[Any] | None = None
        self._run = ""
        # Voz do web: microfone/STT/TTS do proprio motor, atras de /voice/*.
        self.voz = WebVoice(
            getattr(orchestrator, "recorder", None),
            getattr(orchestrator, "transcriber", None),
            getattr(orchestrator, "tts", None),
            logger=logger,
        )

    # ------------------------------------------------------------------ ciclo
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def ocupado(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()

    # ------------------------------------------------------------------ rotas
    def health(self) -> dict[str, Any]:
        cfg = self.orch.cfg
        return {
            "version": getattr(__import__("gaia"), "__version__", ""),
            "model": cfg.agent.model_slug(),
            "session": getattr(self.orch, "session_id", ""),
            # `mode_name` é property no Orchestrator (str), não método: chamar
            # como função levantava TypeError e deixava /health sem resposta.
            "mode": getattr(self.orch, "mode_name", ""),
            "mode_key": self._mode_key(),
            "busy": self.ocupado,
            "chinese": self.chinese(),
            "language": self.echo_language(),
        }

    def sessions(self) -> list[dict[str, Any]]:
        return list(self.orch.sessions())

    # ---------------------------------------------------------------- modelos
    def models(self) -> list[dict[str, Any]]:
        """Modelos disponiveis (mesma lista do /modelo da TUI)."""
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        futuro = asyncio.run_coroutine_threadsafe(self._listar_modelos(), self._loop)
        return futuro.result(timeout=20.0)

    def switch_model(self, slug: str) -> list[str]:
        """Troca o modelo e devolve as mensagens do /modelo."""
        if self.ocupado:
            raise MotorOcupado("turno em andamento")
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        futuro = asyncio.run_coroutine_threadsafe(self._trocar_modelo(slug), self._loop)
        return futuro.result(timeout=60.0)

    async def _listar_modelos(self) -> list[dict[str, Any]]:
        agent = getattr(self.orch, "client", None)
        if agent is None:
            return []
        from gaia.agent.catalog import collect_models

        catalog = await collect_models(self.orch.cfg, client=agent)
        return [
            {
                "slug": option.slug,
                "provider": option.provider,
                "model": option.model,
                "current": option.current,
            }
            for option in catalog.options
        ]

    async def _trocar_modelo(self, slug: str) -> list[str]:
        switch = getattr(self.orch, "model_messages", None)
        if not callable(switch):
            raise RuntimeError("troca de modelo indisponivel neste motor")
        return [text for _level, text in await switch(slug)]

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Busca no Second Brain do motor (lexical + semantica, como na TUI)."""
        index = getattr(self.orch, "index", None)
        if index is None or not query.strip():
            return []
        try:
            hits = index.search(query, limit)
        except Exception as exc:  # busca nao pode derrubar a rota
            logger.warning("busca no Second Brain falhou: %s", exc)
            return []
        return [_plain(hit) for hit in hits]

    # ------------------------------------------------------------------ modos
    def modes(self) -> list[dict[str, Any]]:
        """Personas do nucleo (mesma lista do /modo da TUI)."""
        listar = getattr(self.orch, "modes", None)
        if not callable(listar):
            return []
        atual = self._mode_key()
        return [
            {
                "key": persona.key,
                "name": persona.name,
                "summary": persona.summary,
                "current": persona.key == atual,
            }
            for persona in listar()
        ]

    def select_mode(self, key: str) -> list[str]:
        """Troca o modo ativo e devolve mensagens do /modo."""
        if self.ocupado:
            raise MotorOcupado("turno em andamento")
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        from gaia.controller.modes import get_persona

        persona = get_persona(key)
        if persona is None:
            # `resolve_mode` cai no padrao quando nao conhece a chave; aqui isso
            # seria uma troca silenciosa errada, entao recusamos.
            raise ValueError(f"modo desconhecido: {key}")
        futuro = asyncio.run_coroutine_threadsafe(
            self.orch.select_mode(persona.key), self._loop
        )
        futuro.result(timeout=60.0)
        return [f"modo ativo: {persona.name} — {persona.summary}"]

    # --------------------------------------------------------------- sessoes
    def open_session(self, conversation_id: int) -> list[str]:
        """Abre uma conversa anterior (equivalente a escolher no /sessoes)."""
        if self.ocupado:
            raise MotorOcupado("turno em andamento")
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        futuro = asyncio.run_coroutine_threadsafe(
            self.orch.select_conversation(int(conversation_id)), self._loop
        )
        futuro.result(timeout=60.0)
        return [f"sessao aberta: {conversation_id}"]

    def new_session(self) -> list[str]:
        """Cria uma conversa nova (equivalente ao /novo)."""
        if self.ocupado:
            raise MotorOcupado("turno em andamento")
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        futuro = asyncio.run_coroutine_threadsafe(self.orch.new_session(), self._loop)
        futuro.result(timeout=60.0)
        return ["sessao nova criada"]

    # ------------------------------------------------------------ idiomas/web
    def languages(self) -> list[dict[str, Any]]:
        """Idiomas de eco que este motor sabe traduzir.

        A tela usa a lista para montar o dialog "idiomas". O motor traduz os
        tres de ``ECHO_LANGUAGES`` (zh/en/fr); ``current`` e o escolhido e
        ``enabled`` diz se o eco esta ligado (o idioma sozinho nao traduz).
        """
        atual = self.echo_language()
        ligado = self.chinese()
        return [
            {
                "key": chave,
                "label": rotulo,
                "code": codigo,
                "current": chave == atual,
                "enabled": ligado,
            }
            for chave, (rotulo, codigo) in IDIOMAS_ECO.items()
        ]

    def set_chinese(self, enabled: bool) -> bool:
        """Liga/desliga o eco (estado do processo, como na TUI)."""
        ligado = bool(enabled)
        interface = getattr(self.orch.cfg, "interface", None)
        if interface is None:
            raise RuntimeError("este motor nao tem configuracao de interface")
        interface.chinese_echo = ligado
        return ligado

    def chinese(self) -> bool:
        interface = getattr(self.orch.cfg, "interface", None)
        return bool(getattr(interface, "chinese_echo", False))

    def echo_language(self) -> str:
        """Idioma do eco escolhido (zh | en | fr); configuracao ausente -> zh."""
        interface = getattr(self.orch.cfg, "interface", None)
        return idioma_eco(getattr(interface, "echo_language", "zh"))

    def set_language(self, key: str, enabled: bool | None = None) -> bool:
        """Troca o idioma de eco e/ou liga-desliga o eco (``None`` = so troca).

        A chave vira ``interface.echo_language`` (zh | en | fr) e o eco liga em
        ``interface.chinese_echo`` — escolher um idioma e o que ativa a
        traducao, igual ao dialog do web. Chave desconhecida levanta
        ``ValueError`` (o transporte traduz em 400).
        """
        chave = (key or "").strip().lower()
        if chave not in IDIOMAS_ECO:
            raise ValueError(f"idioma desconhecido: {key}")
        interface = getattr(self.orch.cfg, "interface", None)
        if interface is None:
            raise RuntimeError("este motor nao tem configuracao de interface")
        interface.echo_language = chave
        return self.set_chinese(True if enabled is None else bool(enabled))

    def echo_chinese(self, role: str, text: str) -> None:
        """Traduz em background e emite ``zh``; nunca bloqueia nem derruba o turno.

        Mesmo caminho da TUI (`translate_echo`), mas fora do turno: o web nao
        tem ``run_worker``. A traducao pode falhar (rede, modelo) e o silencio e
        o comportamento certo — a bolha original ja esta na tela.
        """
        de = (text or "").strip()
        if not de or not self.chinese():
            return
        idioma = self.echo_language()
        loop = self._loop
        if loop is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._traduzir(role, de, idioma), loop)
            return
        threading.Thread(
            target=lambda: asyncio.run(self._traduzir(role, de, idioma)),
            name=f"gaia-eco-{idioma}",
            daemon=True,
        ).start()

    async def _traduzir(self, role: str, texto: str, idioma: str) -> None:
        tradutor = getattr(self.orch, "translate_echo", None)
        if callable(tradutor):
            chamada = lambda: tradutor(texto, idioma)  # noqa: E731
        else:
            # Motor antigo: so existe `translate_zh` (e so o chines).
            if idioma != "zh":
                return
            legado = getattr(self.orch, "translate_zh", None)
            if not callable(legado):
                return
            chamada = lambda: legado(texto)  # noqa: E731
        try:
            eco = await asyncio.wait_for(chamada(), timeout=20.0)
        except Exception as exc:  # traducao nunca derruba o turno
            logger.debug("eco %s falhou: %s", idioma, exc)
            return
        if eco:
            # `role`, nao `kind`: `emit(kind, **payload)` ja usa esse nome.
            # `lang` deixa o web rotular a linha; `traducao` e o campo generico
            # e `zh` continua sendo enviado (compatibilidade com telas antigas).
            self.ui.emit(
                "zh",
                role=role,
                text=texto,
                zh=eco,
                traducao=eco,
                lang=idioma,
            )

    def history(self) -> list[dict[str, Any]]:
        """Turnos da conversa ativa (para o web redesenhar ao trocar de sessao)."""
        ler = getattr(self.orch, "history", None)
        if not callable(ler):
            return []
        try:
            return [{"role": role, "text": text} for role, text in ler()]
        except Exception as exc:  # historico nao derruba a rota
            logger.warning("historico falhou: %s", exc)
            return []

    def _mode_key(self) -> str:
        chave = getattr(self.orch, "mode_key", "")
        return chave if isinstance(chave, str) else ""

    def turn(self, text: str) -> str:
        if self.ocupado:
            raise MotorOcupado("turno em andamento")
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        self._run = f"r_{secrets.token_hex(4)}"
        self.ui.run = self._run
        self.ui.emit("run_started", text=text)
        self.echo_chinese("user", text)
        self._tarefa = asyncio.run_coroutine_threadsafe(
            self._executar(text), self._loop
        )
        return self._run

    def decide(self, request_id: str, decision: str) -> None:
        """Gancho do alvo depois que o transporte destravou o `RemoteUI`.

        O transporte é quem destrava (é ele que conhece o `RemoteUI`); aqui só
        registramos, para o dia em que houver auditoria de aprovação.
        """
        logger.debug("permissao %s decidida: %s", request_id, decision)

    def abort(self) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._abortar(), self._loop)

    # -------------------------------------------------------------------- voz
    def voice_status(self) -> dict[str, Any]:
        """O modo voz do web existe nesta maquina? (microfone + STT + TTS)"""
        ok, motivo = self.voz.available()
        # O upload do navegador so precisa do STT: o microfone do motor pode
        # faltar (mini headless) e o modo voz continua util.
        stt_ok, stt_motivo = self.voz.transcription_available()
        fonte = self.voice_source()
        disponivel = stt_ok if fonte == "browser" else ok
        tts = getattr(self.orch, "tts", None)
        return {
            "available": disponivel,
            "reason": "" if disponivel else (stt_motivo if fonte == "browser" else motivo),
            "tts": bool(tts is not None and getattr(tts, "enabled", False)),
            # De onde a tela deve gravar: o microfone do navegador (WAV em
            # POST /voice/upload) ou o do motor (POST /voice/listen).
            "source": fonte,
            "microphone": ok,
            # Fase da escuta em curso: a tela sonda isto para sair de "ouvindo"
            # para "transcrevendo" sem esperar o fim do POST /voice/listen.
            "state": self.voz.state,
        }

    def voice_source(self) -> str:
        """Fonte de audio escolhida em `[serve] voice_source` (browser|server)."""
        cfg = getattr(self.orch, "cfg", None)
        serve = getattr(cfg, "serve", None)
        escolha = str(getattr(serve, "voice_source", "browser") or "browser").lower()
        return escolha if escolha in {"browser", "server"} else "browser"

    def upload_voice(self, wav_bytes: bytes) -> dict[str, Any]:
        """Transcreve um WAV do microfone do navegador (`POST /voice/upload`)."""
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        futuro = asyncio.run_coroutine_threadsafe(
            self.voz.upload(wav_bytes), self._loop
        )
        return futuro.result(timeout=90.0)

    def listen(self, window: float = 15.0) -> dict[str, Any]:
        """Grava uma fala no microfone do motor e devolve o texto transcrito."""
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        segundos = clamp_window(window)
        futuro = asyncio.run_coroutine_threadsafe(
            self.voz.listen(segundos), self._loop
        )
        return futuro.result(timeout=segundos + LISTEN_TIMEOUT_SLACK)

    def stop_listening(self) -> None:
        """Fecha a janela de escuta agora (botao desligado no meio da fala)."""
        self.voz.stop()

    def speak(self, text: str) -> dict[str, Any]:
        """Sintetiza a resposta e devolve ``{audio, mime}`` (base64) para o web."""
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        futuro = asyncio.run_coroutine_threadsafe(self.voz.speech(text), self._loop)
        preparado = futuro.result(timeout=60.0)
        if preparado is None:
            return {"audio": "", "mime": ""}
        dados, tipo = preparado
        return {"audio": base64.b64encode(dados).decode("ascii"), "mime": tipo}

    # --------------------------------------------------------------- backlog
    def backlog_snapshot(self) -> dict[str, Any]:
        """Card do backlog: grupos por projeto + estado da fila do pi."""
        ler = getattr(self.orch, "backlog_snapshot", None)
        if not callable(ler):
            raise RuntimeError("este motor nao tem backlog")
        return dict(ler() or {})

    def backlog_add(self, text: str, project: str = "") -> dict[str, Any]:
        """Item novo pelo chat/voz (o card e o `/backlog add` usam a mesma)."""
        adicionar = getattr(self.orch, "backlog_add", None)
        if not callable(adicionar):
            raise RuntimeError("este motor nao tem backlog")
        return dict(adicionar(text, project) or {})

    def backlog_enqueue(self, item_id: int) -> dict[str, Any]:
        """Enfileira o item no worker do pi (o clique no card)."""
        enfileirar = getattr(self.orch, "backlog_enqueue", None)
        if not callable(enfileirar):
            raise RuntimeError("este motor nao tem backlog")
        return dict(enfileirar(int(item_id)) or {})

    async def backlog_sync(self) -> dict[str, Any]:
        """Le a Agenda e atualiza os itens (botao "Atualizar" do card).

        Assincrono porque busca paginas no Supabase; o transporte espera no
        loop do motor com ``run_awaitable``.
        """
        sincronizar = getattr(self.orch, "backlog_sync", None)
        if not callable(sincronizar):
            raise RuntimeError("este motor nao tem backlog")
        return dict(await sincronizar() or {})

    def backlog_set_repo(self, project: str, repo: str) -> dict[str, Any]:
        """Projeto sem repositorio: salva ``[backlog.repos]`` no TOML."""
        gravar = getattr(self.orch, "backlog_set_repo", None)
        if not callable(gravar):
            raise RuntimeError("este motor nao tem backlog")
        return dict(gravar(project, repo) or {})

    # ------------------------------------------------------------- control center
    def run_awaitable(self, aguardavel: Any, timeout: float = 60.0) -> Any:
        """Executa um awaitable no loop do motor e devolve o resultado.

        O HTTP roda noutra thread; sem esta ponte, ``await`` no handler
        travaria o socket. Mesmo padrao de ``models()``.
        """
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        futuro = asyncio.run_coroutine_threadsafe(aguardavel, self._loop)
        return futuro.result(timeout=timeout)

    # ------------------------------------------------------------------ interno
    async def _executar(self, text: str) -> None:
        try:
            resposta = await self.orch.answer_once(text, speak=False, close=False)
        except Exception as exc:  # nao derruba o servidor por um turno ruim
            logger.warning("turno remoto falhou: %s", exc, exc_info=True)
            self.ui.emit("run_finished", ok=False, error=str(exc))
            return
        self.ui.emit("run_finished", ok=True, text=resposta or "")
        if resposta:
            self.echo_chinese("gaia", resposta)

    async def _abortar(self) -> None:
        abort = getattr(self.orch.client, "abort", None)
        if abort is None:
            return
        resultado = abort(self.orch.session_id)
        if inspect.isawaitable(resultado):
            await resultado
        self.ui.emit("aborted")
