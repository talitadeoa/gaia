"""Adaptador `Orchestrator` -> `Target`: a ponte thread <-> asyncio do serve."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest

from gaia.interface.remote import RemoteUI
from gaia.serve.app import MotorOcupado, OrchestratorTarget


class FakeClient:
    def __init__(self) -> None:
        self.abortados: list[str] = []

    async def abort(self, session_id: str) -> None:
        self.abortados.append(session_id)

    async def list_models(self) -> list[str]:
        return ["flash", "deepseek-v4-flash"]


class FakeOrch:
    def __init__(self, *, falhar: bool = False, demorar: float = 0.0) -> None:
        self.cfg = type(
            "C",
            (),
            {
                "agent": type(
                    "A",
                    (),
                    {
                        "model_slug": lambda self: "deepseek/flash",
                        "provider": "deepseek",
                        "model": "flash",
                        "base_url": "",
                        "api_key": "",
                        "model_copy": lambda self, update: self,
                    },
                )()
            },
        )()
        self.session_id = "sess-1"
        self.channel = "serve"
        self.client = FakeClient()
        self.falhar = falhar
        self.demorar = demorar
        self.textos: list[str] = []

    # Igual ao Orchestrator de verdade: é property, não método. Se virar
    # método de novo, `/health` quebra e este fake não deve mascarar.
    @property
    def mode_name(self) -> str:
        return "assistente"

    def sessions(self) -> list[dict[str, Any]]:
        return [{"id": 1, "channel": "serve"}]

    async def answer_once(self, text: str, **_: Any) -> str:
        self.textos.append(text)
        if self.demorar:
            await asyncio.sleep(self.demorar)
        if self.falhar:
            raise RuntimeError("provedor caiu")
        return f"resposta para {text}"

    async def model_messages(self, argument: str) -> list[tuple[str, str]]:
        self.trocas = getattr(self, "trocas", [])
        self.trocas.append(argument)
        return [("success", f"modelo da próxima mensagem: {argument}")]

    async def aclose(self) -> None:
        pass


@pytest.fixture()
def loop():
    lp = asyncio.new_event_loop()
    thread = threading.Thread(target=lp.run_forever, daemon=True)
    thread.start()
    try:
        yield lp
    finally:
        lp.call_soon_threadsafe(lp.stop)
        thread.join(timeout=3)


def _esperar(ui: RemoteUI, tipo: str, limite: float = 3.0) -> dict:
    fim = time.time() + limite
    while time.time() < fim:
        try:
            evento = ui.events.get(timeout=0.1)
        except Exception:
            continue
        if evento["type"] == tipo:
            return evento
    raise AssertionError(f"evento {tipo} nao chegou")


def test_health_traz_modelo_sessao_e_busy(loop):
    alvo = OrchestratorTarget(FakeOrch(), RemoteUI())
    saude = alvo.health()
    assert saude["model"] == "deepseek/flash"
    assert saude["session"] == "sess-1"
    assert saude["mode"] == "assistente"
    assert saude["busy"] is False


def test_sessions_delega_para_o_orquestrador(loop):
    alvo = OrchestratorTarget(FakeOrch(), RemoteUI())
    assert alvo.sessions()[0]["channel"] == "serve"


def test_models_lista_pelo_catalogo(loop):
    alvo = OrchestratorTarget(FakeOrch(), RemoteUI())
    alvo.attach_loop(loop)

    modelos = alvo.models()

    slugs = [item["slug"] for item in modelos]
    assert "deepseek/flash" in slugs
    assert modelos[0]["current"] is True


def test_switch_model_devolve_as_mensagens(loop):
    orch = FakeOrch()
    alvo = OrchestratorTarget(orch, RemoteUI())
    alvo.attach_loop(loop)

    mensagens = alvo.switch_model("commandcode/deepseek/deepseek-v4-flash")

    assert mensagens == [
        "modelo da próxima mensagem: commandcode/deepseek/deepseek-v4-flash"
    ]
    assert orch.trocas == ["commandcode/deepseek/deepseek-v4-flash"]


def test_switch_model_recusa_com_turno_em_andamento(loop):
    alvo = OrchestratorTarget(FakeOrch(demorar=0.4), RemoteUI())
    alvo.attach_loop(loop)
    alvo.turn("primeiro")
    with pytest.raises(MotorOcupado):
        alvo.switch_model("deepseek/flash")


def test_turn_roda_no_loop_e_emite_run_finished(loop):
    orch = FakeOrch()
    ui = RemoteUI()
    alvo = OrchestratorTarget(orch, ui)
    alvo.attach_loop(loop)

    run = alvo.turn("oi")
    assert run.startswith("r_")
    assert ui.run == run
    inicio = _esperar(ui, "run_started")
    assert inicio["text"] == "oi"

    fim = _esperar(ui, "run_finished")
    assert fim["ok"] is True
    assert fim["text"] == "resposta para oi"
    assert orch.textos == ["oi"]


def test_turn_sem_loop_ligado_falha_alto(loop):
    alvo = OrchestratorTarget(FakeOrch(), RemoteUI())
    with pytest.raises(RuntimeError, match="loop"):
        alvo.turn("oi")


def test_turn_ocupado_levanta_motor_ocupado(loop):
    orch = FakeOrch(demorar=0.4)
    alvo = OrchestratorTarget(orch, RemoteUI())
    alvo.attach_loop(loop)
    alvo.turn("primeiro")
    with pytest.raises(MotorOcupado):
        alvo.turn("segundo")


def test_falha_do_motor_vira_evento_e_nao_derruba_o_processo(loop):
    ui = RemoteUI()
    alvo = OrchestratorTarget(FakeOrch(falhar=True), ui)
    alvo.attach_loop(loop)
    alvo.turn("oi")
    fim = _esperar(ui, "run_finished")
    assert fim["ok"] is False
    assert "provedor caiu" in fim["error"]


def test_decide_destrava_a_permissao_pendente():
    ui = RemoteUI()
    alvo = OrchestratorTarget(FakeOrch(), ui)
    assert alvo.decide("p9", "once") is None  # sem pendente: nao explode


def test_abort_chama_o_client_do_motor(loop):
    orch = FakeOrch()
    ui = RemoteUI()
    alvo = OrchestratorTarget(orch, ui)
    alvo.attach_loop(loop)
    alvo.abort()
    _esperar(ui, "aborted")
    assert orch.client.abortados == ["sess-1"]


def test_eventos_do_servidor_sao_json(loop):
    ui = RemoteUI()
    alvo = OrchestratorTarget(FakeOrch(), ui)
    alvo.attach_loop(loop)
    alvo.turn("oi")
    for tipo in ("run_started", "run_finished"):
        json.dumps(_esperar(ui, tipo))


def test_resolver_de_host_porta_token_prioriza_a_cli():
    from gaia.cli_serve import _resolver
    from gaia.config import Config

    cfg = Config()
    args = type(
        "Args", (), {"host": "203.0.113.10", "port": 9000, "token": "x"}
    )()
    assert _resolver(cfg, args) == ("203.0.113.10", 9000, "x", "")

    vazio = type("Args", (), {"host": None, "port": None, "token": None})()
    assert _resolver(cfg, vazio) == ("127.0.0.1", 8787, "", "")



def test_resolver_do_token_do_painel():
    """O token do Control Center vem do config/.env, nunca do token do chat."""
    from gaia.cli_serve import _resolver
    from gaia.config import Config

    cfg = Config()
    cfg.serve.token = "chat"
    cfg.serve.control_token = "controle"
    args = type("Args", (), {"host": None, "port": None, "token": None})()
    assert _resolver(cfg, args) == ("127.0.0.1", 8787, "chat", "controle")


class _Store:
    def __init__(self) -> None:
        self.fechado = False

    def close(self) -> None:
        self.fechado = True


class _Index(_Store):
    pass


class FakeOrchServico(FakeOrch):
    """Orquestrador falso com o ciclo de vida que o `serve` usa."""

    def __init__(self) -> None:
        super().__init__()
        self.iniciou = False
        self.encerrou = False
        self.transcriber = object()

    async def start_services(self) -> None:
        self.iniciou = True

    async def aclose(self) -> None:
        self.encerrou = True


def _porta_livre() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_serve_command_sobe_http_e_encerra_limpo(monkeypatch):
    import http.client

    from gaia.cli_serve import _serve_command
    from gaia.config import Config

    orch = FakeOrchServico()
    store, index = _Store(), _Index()
    monkeypatch.setattr(
        "gaia.cli_serve._build_app", lambda *a, **k: (orch, store, index)
    )
    porta = _porta_livre()
    cfg = Config()
    cfg.serve.telegram = False
    args = type(
        "Args",
        (),
        {
            "verbose": False,
            "host": "127.0.0.1",
            "port": porta,
            "token": None,
            "no_telegram": True,
        },
    )()

    async def cenario() -> tuple[int, int]:
        parar = asyncio.Event()
        tarefa = asyncio.create_task(_serve_command(cfg, args, parar))
        status = 0
        for _ in range(150):
            await asyncio.sleep(0.02)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", porta, timeout=1)
                conn.request("GET", "/health")
                status = conn.getresponse().status
                conn.close()
                break
            except OSError:
                continue
        parar.set()
        return await tarefa, status

    codigo, status = asyncio.run(cenario())
    assert status == 200
    assert codigo == 0
    assert orch.iniciou is True
    assert orch.encerrou is True
    assert store.fechado is True
    assert index.fechado is True


def test_serve_command_recusa_bind_na_rede_sem_token(monkeypatch):
    from gaia.cli_serve import _serve_command
    from gaia.config import Config

    orch = FakeOrchServico()
    monkeypatch.setattr(
        "gaia.cli_serve._build_app", lambda *a, **k: (orch, _Store(), _Index())
    )
    cfg = Config()
    cfg.serve.telegram = False
    args = type(
        "Args",
        (),
        {
            "verbose": False,
            "host": "203.0.113.10",
            "port": 0,
            "token": None,
            "no_telegram": True,
        },
    )()
    assert asyncio.run(_serve_command(cfg, args, asyncio.Event())) == 2
    assert orch.encerrou is True


def test_search_traduz_hits_do_indice_para_json():
    """A busca da web usa o mesmo índice da TUI (dataclass -> dict)."""
    from dataclasses import dataclass

    @dataclass
    class Hit:
        path: str
        rel_path: str
        title: str
        snippet: str
        tags: list[str]
        score: float
        section: str

    class Index:
        def __init__(self) -> None:
            self.pedidos: list[tuple[str, int]] = []

        def search(self, query: str, limit: int = 5) -> list[Hit]:
            self.pedidos.append((query, limit))
            return [Hit("p.md", "Memoria/p.md", "Impulsão", "vontade", ["x"], 0.2, "Corpo")]

    orch = FakeOrch()
    orch.index = Index()
    alvo = OrchestratorTarget(orch, RemoteUI())

    hits = alvo.search("impulsão", 4)

    assert orch.index.pedidos == [("impulsão", 4)]
    assert hits[0]["rel_path"] == "Memoria/p.md"
    assert hits[0]["tags"] == ["x"]


def test_search_nao_derruba_a_rota_quando_o_indice_falha():
    class IndexQuebrado:
        def search(self, query: str, limit: int = 5) -> list[Any]:
            raise RuntimeError("disco sumiu")

    orch = FakeOrch()
    orch.index = IndexQuebrado()
    alvo = OrchestratorTarget(orch, RemoteUI())
    assert alvo.search("x") == []


def test_search_com_query_vazia_nao_toca_o_indice():
    class Index:
        def __init__(self) -> None:
            self.chamado = False

        def search(self, query: str, limit: int = 5) -> list[Any]:
            self.chamado = True
            return []

    orch = FakeOrch()
    orch.index = Index()
    alvo = OrchestratorTarget(orch, RemoteUI())
    assert alvo.search("   ") == []
    assert orch.index.chamado is False


# ------------------------------------------------------------------- bots ---
class BotsOrch(FakeOrch):
    """Motor falso com o registry de bots e a troca por id."""

    def __init__(self, *, indisponivel: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bot_ativo = "gaia"
        self.indisponivel = indisponivel
        self.nome_ativo = "Gaia"

    def bots_list(self) -> list:
        from gaia.bots import get_bot

        return [get_bot("gaia"), get_bot("donna")]

    def bot_status(self, bot) -> tuple[bool, str]:
        if bot.id == "donna" and self.indisponivel:
            return False, "workspace inexistente: ~/dissertacao"
        return True, ""

    def bot_for(self, key: str | None):
        from gaia.bots import get_bot

        return get_bot(key)

    async def select_bot(self, key: str):
        from gaia.bots import BotIndisponivel, require_bot

        bot = require_bot(key)
        disponivel, motivo = self.bot_status(bot)
        if not disponivel:
            raise BotIndisponivel(f"o bot {bot.name} nao esta disponivel: {motivo}")
        self.bot_ativo = bot.id
        self.nome_ativo = bot.name
        return bot

    @property
    def mode_key(self) -> str:
        return self.bot_ativo

    @property
    def mode_name(self) -> str:
        return self.nome_ativo


def test_health_traz_o_bot_ativo(loop):
    alvo = OrchestratorTarget(BotsOrch(), RemoteUI())
    saude = alvo.health()
    assert saude["bot"] == saude["mode_key"]


def test_bots_lista_com_disponibilidade_e_atual(loop):
    alvo = OrchestratorTarget(BotsOrch(), RemoteUI())
    alvo.attach_loop(loop)

    bots = alvo.bots()

    assert [b["id"] for b in bots] == ["gaia", "donna"]
    assert bots[0]["current"] is True
    assert bots[0]["tools"] is None  # Gaia = catalogo inteiro
    assert "read" in bots[1]["tools"]
    assert bots[1]["available"] is True
    # `/modes` e a mesma lista com as chaves historicas.
    modos = alvo.modes()
    assert [m["key"] for m in modos] == ["gaia", "donna"]
    assert modos[0]["summary"] == bots[0]["role"]


def test_select_bot_troca_e_devolve_mensagem(loop):
    orch = BotsOrch()
    alvo = OrchestratorTarget(orch, RemoteUI())
    alvo.attach_loop(loop)

    mensagens = alvo.select_bot("donna")

    assert orch.bot_ativo == "donna"
    assert mensagens == ["bot ativo: Donna Haraway — dissertação, ensaios, revisão e método"]


def test_select_bot_desconhecido_levanta_value_error(loop):
    alvo = OrchestratorTarget(BotsOrch(), RemoteUI())
    alvo.attach_loop(loop)
    with pytest.raises(ValueError):
        alvo.select_bot("maria")


def test_select_bot_indisponivel_levanta_bot_indisponivel(loop):
    from gaia.bots import BotIndisponivel

    alvo = OrchestratorTarget(BotsOrch(indisponivel=True), RemoteUI())
    alvo.attach_loop(loop)
    with pytest.raises(BotIndisponivel, match="nao esta disponivel"):
        alvo.select_bot("donna")


def test_select_bot_recusa_com_turno_em_andamento(loop):
    alvo = OrchestratorTarget(BotsOrch(demorar=0.4), RemoteUI())
    alvo.attach_loop(loop)
    alvo.turn("primeiro")
    with pytest.raises(MotorOcupado):
        alvo.select_bot("donna")
