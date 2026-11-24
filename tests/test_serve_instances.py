"""Instancias do `instances.json` e sessao de cookie do chat.

Dois contratos que a tela depende e que sao faceis de quebrar em silencio:

* ``GET /instances`` e **publico** e **nunca** devolve token (nem mascarado);
  status online/offline e medido pelo servidor, com timeout curto.
* ``POST /session`` troca o ``Bearer`` do chat por cookie ``HttpOnly``; o
  ``Bearer`` continua aceito (cliente fino e ``curl``) e o cookie do painel
  **nao** vale no chat (nem o contrario).
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any

import pytest

from gaia.interface.remote import RemoteUI
from gaia.serve import instances as instances_mod
from gaia.serve.server import ServeServer


class FakeTarget:
    def __init__(self) -> None:
        self.modo = "gaia"
        self.abriu: list[int] = []
        self.novas = 0
        self.vistos: list[str] = []

    def health(self) -> dict[str, Any]:
        return {
            "model": "deepseek/flash",
            "mode": "Assistente",
            "version": "0.1.0",
            "busy": False,
            "session": "s1",
        }

    def sessions(self) -> list[dict[str, Any]]:
        return [
            {"id": 1, "title": "conversa", "updated_at": "2026-11-22T10:00:00",
             "turns": 4, "session_id": "s1", "current": True}
        ]

    def models(self) -> list[dict[str, Any]]:
        return [{"slug": "deepseek/flash", "current": True}]

    def switch_model(self, slug: str) -> list[str]:
        return [f"modelo: {slug}"]

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    def turn(self, text: str) -> str:
        return "r_1"

    def decide(self, request_id: str, decision: str) -> None:
        return None

    def abort(self) -> None:
        return None

    # rotas novas -------------------------------------------------------
    def modes(self) -> list[dict[str, Any]]:
        """Alvo antigo: so `/modes`; `/bots` cai neste mesmo metodo."""
        from gaia.bots import BotIndisponivel, get_bot

        atual = self.modo
        itens = []
        for bot in (get_bot("gaia"), get_bot("donna")):
            assert bot is not None
            itens.append(
                {
                    "key": bot.id,
                    "id": bot.id,
                    "name": bot.name,
                    "summary": bot.role,
                    "current": bot.id == atual,
                    "available": bot.id != "donna",
                    "reason": "workspace inexistente" if bot.id == "donna" else "",
                }
            )
        assert BotIndisponivel  # a excecao e tratada em outro teste
        return itens

    def select_mode(self, key: str) -> list[str]:
        from gaia.bots import get_bot

        bot = get_bot(key)
        if bot is None:
            raise ValueError(f"modo desconhecido: {key}")
        self.modo = bot.id
        return [f"modo ativo: {bot.name}"]

    def open_session(self, conversation_id: int) -> list[str]:
        self.abriu.append(conversation_id)
        return [f"sessao aberta: {conversation_id}"]

    def new_session(self) -> list[str]:
        self.novas += 1
        return ["sessao nova criada"]

    def history(self) -> list[dict[str, Any]]:
        return [
            {"role": "user", "text": "oi"},
            {"role": "assistant", "text": "olá"},
        ]

    # feed de atividade (fase 2) -----------------------------------------
    def activity(self, limit: int = 50, bot: str = "") -> dict[str, Any]:
        mensagens = [
            {"id": 2, "bot": "donna", "kind": "job", "title": "Capitulo 2",
             "text": "revisado", "when": "2026-11-25T10:00:00", "seen": False},
            {"id": 1, "bot": "ariana", "kind": "job", "title": "Carteira",
             "text": "PETR4 +2%", "when": "2026-11-25T09:00:00", "seen": True},
        ]
        if bot:
            mensagens = [item for item in mensagens if item["bot"] == bot]
        return {
            "messages": mensagens[:limit],
            "runs": [
                {"id": 7, "bot": "donna", "kind": "bot_task", "status": "completed",
                 "automation": "user:revisao", "when": "2026-11-25T10:00:00",
                 "detail": "ok", "error": ""},
            ],
            "unread": {"donna": 1},
        }

    def mark_seen(self, bot: str = "") -> dict[str, Any]:
        self.vistos.append(bot)
        return {"ok": True, "unread": {} if not bot else {"ariana": 1}}


def _pedir(
    base: str,
    metodo: str,
    caminho: str,
    *,
    token: str = "",
    corpo: dict[str, Any] | None = None,
    cookie: str = "",
) -> tuple[int, Any, str]:
    host, porta = base.removeprefix("http://").split(":")
    conexao = http.client.HTTPConnection(host, int(porta), timeout=10)
    cabecalhos: dict[str, str] = {}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    if cookie:
        cabecalhos["Cookie"] = cookie
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        cabecalhos["Content-Type"] = "application/json"
    conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conexao.getresponse()
    bruto = resposta.read()
    set_cookie = resposta.getheader("Set-Cookie") or ""
    conexao.close()
    try:
        return resposta.status, json.loads(bruto), set_cookie
    except ValueError:
        return resposta.status, bruto, set_cookie


def _cookie_de(set_cookie: str) -> str:
    """`gaia_chat=nonce; Path=...` -> `gaia_chat=nonce` (o que o navegador manda)."""
    return set_cookie.split(";", 1)[0]


@pytest.fixture()
def servidor(tmp_path: Path):
    alvo = FakeTarget()
    srv = ServeServer(
        alvo,  # type: ignore[arg-type]
        RemoteUI(),
        host="127.0.0.1",
        port=0,
        token="tok-chat",
        control_token="ctl-secreto",
        instance_id="mini",
        instances_path=tmp_path / "instances.json",
    )
    base = srv.start()
    yield srv, alvo, base, tmp_path / "instances.json"
    srv.shutdown()


# ------------------------------------------------------------------ arquivo --

def test_arquivo_ausente_e_lista_vazia(tmp_path: Path):
    assert instances_mod.load(tmp_path / "nao-existe.json") == []


def test_arquivo_invalido_nao_levanta(tmp_path: Path):
    ruim = tmp_path / "instances.json"
    ruim.write_text("{nao e json", encoding="utf-8")
    assert instances_mod.load(ruim) == []
    ruim.write_text('{"id": "x"}', encoding="utf-8")
    assert instances_mod.load(ruim) == []


def test_itens_incompletos_sao_descartados(tmp_path: Path):
    arquivo = tmp_path / "instances.json"
    arquivo.write_text(
        json.dumps(
            [
                {"name": "sem id", "url": "http://a:8787"},
                {"id": "sem-url"},
                {"id": "a", "url": "javascript:alert(1)"},
                {"id": "mini", "url": "mini.local:8787", "serve_token": "t1"},
                "lixo",
                {"id": "mini", "name": "duplicado", "url": "http://dup:8787"},
            ]
        ),
        encoding="utf-8",
    )
    itens = instances_mod.load(arquivo)
    assert [item.id for item in itens] == ["mini"]
    # URL sem esquema vira http e a barra final sai.
    assert itens[0].url == "http://mini.local:8787"
    assert itens[0].name == "mini"


def test_public_nao_expoe_token(tmp_path: Path):
    empresa = instances_mod.Instance(
        id="mac", name="Mac", url="http://mac:8787", serve_token="s3", control_token="c3"
    )
    publico = empresa.public()
    assert publico["has_serve_token"] is True
    assert publico["has_control_token"] is True
    assert "s3" not in json.dumps(publico)
    assert "c3" not in json.dumps(publico)


def test_current_pelo_host_e_pelo_instance_id(tmp_path: Path):
    itens = [
        instances_mod.Instance(id="mini", name="Mini", url="http://mini.local:8787"),
        instances_mod.Instance(id="mac", name="Mac", url="http://mac.local:8787"),
    ]
    por_host = instances_mod.marcar_atual(itens, "mini.local:8787")
    assert [item.current for item in por_host] == [True, False]
    sem_casar = instances_mod.marcar_atual(itens, "outro-host:8787")
    assert not any(item.current for item in sem_casar)
    por_id = instances_mod.marcar_atual(itens, "", "mac")
    assert [item.current for item in por_id] == [False, True]


# ------------------------------------------------------------------- rotas ---

def test_instances_e_publica_e_omite_tokens(servidor):
    _, _, base, arquivo = servidor
    outras = ServeServer(
        FakeTarget(),  # type: ignore[arg-type]
        RemoteUI(),
        host="127.0.0.1",
        port=0,
        token="segredo-do-mini",
    )
    url_outra = outras.start()
    try:
        arquivo.write_text(
            json.dumps(
                [
                    {"id": "mini", "name": "Mini PC", "url": url_outra,
                     "serve_token": "segredo-do-mini", "control_token": "ctl-x"},
                    {"id": "mac", "name": "Mac", "url": "http://127.0.0.1:1"},
                ]
            ),
            encoding="utf-8",
        )
        status, corpo, _ = _pedir(base, "GET", "/instances")
        assert status == 200
        itens = {item["id"]: item for item in corpo["instances"]}
        # O host do pedido nao casa com nenhuma URL: nenhuma "current", mas a
        # identidade deste servidor (instance_id) continua marcada no /engine.
        assert itens["mini"]["online"] is True
        assert itens["mini"]["model"] == "deepseek/flash"
        assert itens["mac"]["online"] is False
        assert "servidores" not in corpo
        texto = json.dumps(corpo)
        assert "segredo-do-mini" not in texto
        assert "ctl-x" not in texto
    finally:
        outras.shutdown()


def test_instances_sem_arquivo_devolve_lista_vazia(servidor):
    _, _, base, _ = servidor
    status, corpo, _ = _pedir(base, "GET", "/instances")
    assert status == 200
    assert corpo["instances"] == []


# ------------------------------------------------------------------ sessao ---

def test_session_troca_bearer_por_cookie(servidor):
    _, _, base, _ = servidor
    status, corpo, set_cookie = _pedir(base, "POST", "/session", token="tok-chat", corpo={})
    assert status == 200
    assert corpo["session"] is True
    assert "HttpOnly" in set_cookie
    assert "gaia_chat=" in set_cookie
    cookie = _cookie_de(set_cookie)

    # Cookie autentica sozinho...
    assert _pedir(base, "GET", "/sessions", cookie=cookie)[0] == 200
    # ...e o Bearer continua valendo (cliente fino/curl).
    assert _pedir(base, "GET", "/sessions", token="tok-chat")[0] == 200
    # GET /session so diz se ha prova valida.
    status, corpo, _ = _pedir(base, "GET", "/session", cookie=cookie)
    assert status == 200 and corpo["authenticated"] is True and corpo["mode"] == "session"


def test_session_recusa_token_errado(servidor):
    _, _, base, _ = servidor
    status, corpo, set_cookie = _pedir(base, "POST", "/session", token="errado", corpo={})
    assert status == 401
    assert set_cookie == ""
    assert corpo["error"] == "unauthorized"


def test_delete_session_encerra_o_nonnce(servidor):
    _, _, base, _ = servidor
    _, _, set_cookie = _pedir(base, "POST", "/session", token="tok-chat", corpo={})
    cookie = _cookie_de(set_cookie)
    status, _, limpar = _pedir(base, "DELETE", "/session", cookie=cookie)
    assert status == 200
    assert "Max-Age=0" in limpar
    assert _pedir(base, "GET", "/sessions", cookie=cookie)[0] == 401


def test_cookie_do_painel_nao_vale_no_chat_nem_o_contrario(servidor):
    _, _, base, _ = servidor
    _, _, do_chat = _pedir(base, "POST", "/session", token="tok-chat", corpo={})
    _, _, do_painel = _pedir(
        base, "POST", "/control/api/session", token="ctl-secreto", corpo={}
    )
    cookie_chat = _cookie_de(do_chat)
    cookie_painel = _cookie_de(do_painel)
    assert cookie_chat.startswith("gaia_chat=")
    assert cookie_painel.startswith("gaia_control=")
    assert _pedir(base, "GET", "/sessions", cookie=cookie_painel)[0] == 401
    assert _pedir(base, "GET", "/control/api/overview", cookie=cookie_chat)[0] == 401


def test_session_sem_token_configurado_e_noop(tmp_path: Path):
    """Loopback sem token: nao ha o que trocar, a rota so confirma."""
    srv = ServeServer(FakeTarget(), RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = srv.start()
    try:
        status, corpo, set_cookie = _pedir(base, "POST", "/session", corpo={})
        assert status == 200
        assert corpo["authenticated"] is True and corpo["session"] is False
        assert set_cookie == ""
    finally:
        srv.shutdown()


# ------------------------------------------------------------ paridade TUI ---

def test_engine_traz_modelo_modo_e_instancia(servidor):
    _, _, base, _ = servidor
    status, corpo, _ = _pedir(base, "GET", "/engine", token="tok-chat")
    assert status == 200
    assert corpo["model"] == "deepseek/flash"
    assert corpo["mode"] == "Assistente"
    # Sem `instances.json`, a identidade vem de `serve.instance_id`.
    assert corpo["instance"]["id"] == "mini"
    assert corpo["control_enabled"] is True
    # Sem painel nao ha como perguntar provedor; o campo fica vazio, nao quebra.
    assert corpo["provider"] == ""


def test_engine_exige_credencial(servidor):
    _, _, base, _ = servidor
    assert _pedir(base, "GET", "/engine")[0] == 401
    # O cookie do painel nao abre rota do chat (separacao de poderes).
    _, _, do_painel = _pedir(
        base, "POST", "/control/api/session", token="ctl-secreto", corpo={}
    )
    assert _pedir(base, "GET", "/engine", cookie=_cookie_de(do_painel))[0] == 401


def test_bots_lista_e_troca(servidor):
    _, alvo, base, _ = servidor
    status, corpo, _ = _pedir(base, "GET", "/bots", token="tok-chat")
    assert status == 200
    assert [b["id"] for b in corpo["bots"]] == ["gaia", "donna"]
    assert corpo["bots"][0]["current"] is True
    # Donna aparece indisponivel com motivo -- a tela pode explicar o clique.
    assert corpo["bots"][1]["available"] is False
    assert "workspace" in corpo["bots"][1]["reason"]

    status, corpo, _ = _pedir(
        base, "POST", "/bot/select", token="tok-chat", corpo={"id": "donna"}
    )
    assert status == 200
    assert alvo.modo == "donna"
    assert corpo["bot"] == "donna"
    # FakeTarget nao tem `select_bot` (alvo antigo): o transporte cai no
    # `select_mode`, que responde pelo mesmo registry.
    assert corpo["messages"] == ["modo ativo: Donna Haraway"]
    # A resposta traz o que a tela redesenha (bolhas, sessoes e bots).
    assert corpo["history"] == [{"role": "user", "text": "oi"},
                                {"role": "assistant", "text": "olá"}]
    assert isinstance(corpo["sessions"], list)
    assert corpo["bots"][1]["current"] is True

    # `/modes` continua (compatibilidade) e le a MESMA lista.
    status, corpo, _ = _pedir(base, "GET", "/modes", token="tok-chat")
    assert status == 200
    assert [m["key"] for m in corpo["modes"]] == ["gaia", "donna"]
    assert corpo["modes"][1]["current"] is True


def test_bot_desconhecido_e_400(servidor):
    _, alvo, base, _ = servidor
    status, corpo, _ = _pedir(
        base, "POST", "/bot/select", token="tok-chat", corpo={"id": "inexistente"}
    )
    assert status == 400
    assert corpo["error"] == "bot_failed"
    assert alvo.modo == "gaia"


def test_bot_vazio_e_400(servidor):
    _, _, base, _ = servidor
    status, corpo, _ = _pedir(base, "POST", "/bot/select", token="tok-chat", corpo={})
    assert status == 400
    assert corpo["error"] == "empty_bot"


def test_mode_lista_e_troca(servidor):
    """`POST /mode` e alias de `/bot/select` (mesmo registry)."""
    _, alvo, base, _ = servidor
    status, corpo, _ = _pedir(base, "GET", "/modes", token="tok-chat")
    assert status == 200
    assert [m["key"] for m in corpo["modes"]] == ["gaia", "donna"]
    assert corpo["modes"][0]["current"] is True

    status, corpo, _ = _pedir(
        base, "POST", "/mode", token="tok-chat", corpo={"mode": "donna"}
    )
    assert status == 200
    assert alvo.modo == "donna"
    assert corpo["messages"] == ["modo ativo: Donna Haraway"]
    assert corpo["engine"]["mode"] == "Assistente"  # health fake nao muda


def test_mode_desconhecido_e_400(servidor):
    _, alvo, base, _ = servidor
    status, corpo, _ = _pedir(
        base, "POST", "/mode", token="tok-chat", corpo={"mode": "inexistente"}
    )
    assert status == 400
    assert corpo["error"] == "mode_failed"
    assert alvo.modo == "gaia"


def test_mode_vazio_e_400(servidor):
    _, _, base, _ = servidor
    status, corpo, _ = _pedir(base, "POST", "/mode", token="tok-chat", corpo={})
    assert status == 400
    assert corpo["error"] == "empty_mode"


def test_sessions_abre_e_cria(servidor):
    _, alvo, base, _ = servidor
    status, corpo, _ = _pedir(
        base, "POST", "/session/open", token="tok-chat", corpo={"id": 7}
    )
    assert status == 200
    assert alvo.abriu == [7]
    assert corpo["history"][0] == {"role": "user", "text": "oi"}

    status, _, _ = _pedir(base, "POST", "/session/new", token="tok-chat", corpo={})
    assert status == 200
    assert alvo.novas == 1


def test_session_open_sem_id_e_400(servidor):
    _, alvo, base, _ = servidor
    status, corpo, _ = _pedir(base, "POST", "/session/open", token="tok-chat", corpo={})
    assert status == 400
    assert corpo["error"] == "bad_id"
    assert alvo.abriu == []


def test_automations_exige_painel(servidor):
    """Sem ControlCenter nao existe agenda: 503 com instrucao, nao 500."""
    _, _, base, _ = servidor
    status, corpo, _ = _pedir(base, "GET", "/automations", token="tok-chat")
    assert status == 503
    assert corpo["error"] == "no_center"


def test_assets_compartilhados(servidor):
    _, _, base, _ = servidor
    status, corpo, _ = _pedir(base, "GET", "/static/shared.js")
    assert status == 200
    assert b"window.Gaia" in corpo
    assert _pedir(base, "GET", "/control/static/shared.js")[0] == 200
    # Allowlist: nada de servir o proprio instances.json.
    assert _pedir(base, "GET", "/static/instances.json")[0] == 404


# ------------------------------------------------- centro de controle (fake) --

class FakeCenter:
    """Só o que as rotas compartilhadas precisam: agenda e provedor atual."""

    def providers(self) -> dict[str, Any]:
        return {
            "providers": [
                {"name": "deepseek", "current": True, "masked": "sk-…1234",
                 "has_key": True, "auth_source": ".env", "local": False},
            ],
            "agent": {"provider": "deepseek"},
        }

    def automations(self) -> dict[str, Any]:
        return {
            "automations": [
                {"id": "briefing-manha", "name": "Briefing da manhã", "enabled": True,
                 "status": "scheduled", "next_run": "2026-11-23T07:00:00-03:00",
                 "last_run": None},
            ]
        }


def test_automations_e_engine_com_centro(tmp_path: Path):
    srv = ServeServer(
        FakeTarget(),  # type: ignore[arg-type]
        RemoteUI(),
        host="127.0.0.1",
        port=0,
        token="tok-chat",
        control_token="ctl-secreto",
        center=FakeCenter(),
    )
    base = srv.start()
    try:
        status, corpo, _ = _pedir(base, "GET", "/automations", token="tok-chat")
        assert status == 200
        assert corpo["automations"][0]["id"] == "briefing-manha"

        status, corpo, _ = _pedir(base, "GET", "/providers", token="tok-chat")
        assert status == 200
        assert corpo["providers"][0]["masked"] == "sk-…1234"

        status, corpo, _ = _pedir(base, "GET", "/engine", token="tok-chat")
        assert status == 200
        assert corpo["provider"] == "deepseek"
        assert corpo["masked"] == "sk-…1234"
        assert corpo["has_key"] is True
        # O proprio center router do painel tambem serve o cabecalho, dentro do
        # path do cookie `gaia_control`.
        status, mesmo, _ = _pedir(
            base, "GET", "/control/api/engine", token="ctl-secreto"
        )
        assert status == 200
        assert mesmo["model"] == corpo["model"]
    finally:
        srv.shutdown()


# ------------------------------------------------------- atividade (fase 2) ---


def test_bots_activity_devolve_o_feed_e_exige_credencial(servidor):
    _, _, base, _ = servidor
    assert _pedir(base, "GET", "/bots/activity")[0] == 401

    status, corpo, _ = _pedir(base, "GET", "/bots/activity?limit=10", token="tok-chat")
    assert status == 200
    assert [m["bot"] for m in corpo["messages"]] == ["donna", "ariana"]
    assert corpo["unread"] == {"donna": 1}
    assert corpo["runs"][0]["bot"] == "donna"

    # Filtro por bot: o card do aside mostra so o ativo.
    status, corpo, _ = _pedir(
        base, "GET", "/bots/activity?bot=ariana", token="tok-chat"
    )
    assert status == 200
    assert [m["bot"] for m in corpo["messages"]] == ["ariana"]




def test_bot_seen_zera_o_badge(servidor):
    _, alvo, base, _ = servidor
    status, corpo, _ = _pedir(
        base, "POST", "/bots/seen", token="tok-chat", corpo={"bot": "donna"}
    )
    assert status == 200
    assert corpo == {"ok": True, "unread": {"ariana": 1}}
    assert alvo.vistos == ["donna"]

    status, corpo, _ = _pedir(base, "POST", "/bots/seen", token="tok-chat", corpo={})
    assert status == 200
    assert corpo["unread"] == {}
    assert alvo.vistos == ["donna", ""]
