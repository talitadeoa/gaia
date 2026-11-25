"""Feed de atividade dos bots e segundo plano por prompt (fase 2/3).

O que estes testes protegem:

* ``bot_activity`` e leitura pura: mensagens + runs por bot, `unread` e ordem;
* ``bot_seen`` zera o badge (por bot e global);
* ``run_bot_task`` roda na sessao do bot (politica dele) e grava no feed;
  falha vira mensagem e run `failed` com `error_kind`;
* ``bot_busy`` e por bot: a automacao da Ariana nao espera a Ada;
* `BotBus` respeita `can_message`, profundidade e `to == from`.
"""

from __future__ import annotations

import asyncio
import logging
import types
from pathlib import Path
from typing import Any

import pytest

from gaia.bots import DEFAULT_BOTS, Bot, BotIndisponivel
from gaia.bots.bus import MAX_DEPTH, BotBus
from gaia.config import BotConfig, Config
from gaia.controller.orchestrator import Orchestrator
from gaia.memory.store import MemoryStore


def _ui() -> Any:
    class _UI:
        def __init__(self) -> None:
            self.eventos: list[tuple[str, dict[str, Any]]] = []

        def __getattr__(self, nome: str):
            if nome == "emit":
                return lambda tipo, **dados: self.eventos.append((tipo, dados))
            return lambda *a, **k: None

    return _UI()


def _orquestrador(tmp_path: Path) -> tuple[Orchestrator, MemoryStore, Any]:
    """Orchestrator minimo, com as pastas dos bots dentro de ``tmp_path``."""
    from gaia.agent import AgentClient

    for nome in ("proj", "donna", "pr-workspace", "dados", "brain"):
        (tmp_path / nome).mkdir(parents=True, exist_ok=True)
    (tmp_path / "brain" / "preferences.md").write_text("", encoding="utf-8")

    class _Brain:
        """So o que `_preferences_block` le (preferencias da pessoa)."""

        root = tmp_path / "brain"
        preferences_path = tmp_path / "brain" / "preferences.md"

        def load_preferences(self) -> str:
            return ""

    class _Index:
        def count(self) -> int:
            return 0

        def search(self, query: str, limit: int = 3):
            return []
    cfg = Config.load(
        config_path=tmp_path / "missing.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.bots = {
        "donna": BotConfig(
            workspace=str(tmp_path / "donna"), scope=str(tmp_path / "donna")
        ),
        "ada": BotConfig(
            workspace=str(tmp_path / "pr-workspace"),
            scope=str(tmp_path / "pr-workspace"),
        ),
        "ariana": BotConfig(
            workspace=str(tmp_path / "dados"), scope=str(tmp_path / "dados")
        ),
    }
    store = MemoryStore(tmp_path / "gaia.db")
    client = AgentClient(cfg, tmp_path / "proj")
    orch = Orchestrator(
        cfg=cfg,
        logger=logging.getLogger("test.bots.activity"),
        ui=_ui(),
        store=store,
        index=_Index(),
        brain=_Brain(),
        tts=None,
        transcriber=None,
        recorder=types.SimpleNamespace(),
        client=client,
    )
    return orch, store, client


# ------------------------------------------------------------------ feed ---


async def test_bot_activity_le_mensagens_runs_e_nao_lidos(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    try:
        store.add_bot_message("ariana", "job", "Carteira", "3 altas, 1 alerta")
        store.add_bot_message("ada", "job", "PR", "#123 aberto")
        run = store.begin_run(
            kind="bot_task", automation="user:x", bot="ariana", trigger="manual"
        )
        store.finish_run(run, status="completed", detail="resumo")

        dados = orch.bot_activity(limit=10)

        assert [m["bot"] for m in dados["messages"]] == ["ada", "ariana"]
        assert dados["messages"][1]["title"] == "Carteira"
        assert dados["unread"] == {"ariana": 1, "ada": 1}
        assert [r["bot"] for r in dados["runs"]] == ["ariana"]
        assert dados["runs"][0]["status"] == "completed"

        # Filtro por bot (o card do aside mostra so o ativo).
        so_ariana = orch.bot_activity(limit=10, bot="ariana")
        assert [m["bot"] for m in so_ariana["messages"]] == ["ariana"]
        assert [r["bot"] for r in so_ariana["runs"]] == ["ariana"]
        # Leitura nao apaga nada.
        assert store.unread_by_bot() == {"ariana": 1, "ada": 1}
    finally:
        store.close()
        await client.aclose()


async def test_bot_seen_zera_o_badge_por_bot_e_no_total(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    try:
        store.add_bot_message("ariana", "job", "Carteira", "resumo")
        store.add_bot_message("ada", "job", "PR", "#1")

        resultado = orch.bot_seen("ariana")

        assert resultado["ok"] is True
        assert resultado["unread"] == {"ada": 1}
        todos = orch.bot_seen("")
        assert todos["unread"] == {}
        assert orch.bot_activity()["messages"][0]["seen"] is True
    finally:
        store.close()
        await client.aclose()


async def test_bot_message_grava_e_emite_bot_activity(tmp_path, isolated_env):
    """O evento do SSE leva o bot e a contagem nova (badge do rail)."""
    orch, store, client = _orquestrador(tmp_path)
    try:
        mensagem_id = orch.bot_message("ariana", "job", "Bolsa", "PETR4 +2%")

        assert mensagem_id > 0
        eventos = [dados for tipo, dados in orch.ui.eventos if tipo == "bot_activity"]
        assert eventos and eventos[-1]["bot"] == "ariana"
        assert eventos[-1]["kind"] == "job"
        assert eventos[-1]["unread"] == {"ariana": 1}
    finally:
        store.close()
        await client.aclose()


# ------------------------------------------------------ segundo plano ---


class _ClientResposta:
    """Fake do AgentClient: responde o prompt na fila da sessao."""

    model = "fake/model"

    def __init__(self, texto: str = "resposta do bot", falhar: bool = False) -> None:
        self.texto = texto
        self.falhar = falhar
        self.prompts: list[tuple[str, str]] = []
        self.permissoes: list[tuple[str, str]] = []
        self.politicas: dict[str, Any] = {}
        self.filas: dict[str, list[asyncio.Queue]] = {}
        self.pedir_permissao = False
        self._criadas = 0

    async def create_session(self, **kwargs):
        self._criadas += 1
        return types.SimpleNamespace(id=f"ses_job{self._criadas}")

    async def get_session(self, session_id):
        if session_id not in self.filas:
            return None
        return types.SimpleNamespace(id=session_id, directory="", title="")

    def set_session_policy(self, session_id, policy) -> None:
        self.politicas[session_id] = policy

    async def prompt_async(self, session_id, text, **kwargs):
        from gaia.agent.events import PermissionAsked, TextDelta, TurnCompleted

        self.prompts.append((session_id, text))
        # Deixa o pump do turno assinar a fila antes de emitir (no motor real
        # `prompt_async` so cria a task e volta; aqui a resposta e sincrona).
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        filas = self.filas.get(session_id, [])
        if self.falhar:
            from gaia.agent.events import TurnFailed

            for fila in filas:
                await fila.put(TurnFailed(session_id=session_id, message="provedor caiu"))
            return
        if self.pedir_permissao:
            for fila in filas:
                await fila.put(
                    PermissionAsked(
                        session_id=session_id, request_id="per_1", tool="bash"
                    )
                )
        for fila in filas:
            await fila.put(TextDelta(session_id=session_id, text=self.texto))
            await fila.put(
                TurnCompleted(session_id=session_id, message_id="m1", text=self.texto)
            )

    async def reply_permission(self, session_id, permission_id, response, **kwargs):
        self.permissoes.append((permission_id, response))

    def subscribe_session(self, session_id):
        fila: asyncio.Queue[Any] = asyncio.Queue()
        self.filas.setdefault(session_id, []).append(fila)
        return fila

    def unsubscribe_session(self, session_id, fila):
        filas = self.filas.get(session_id, [])
        if fila in filas:
            filas.remove(fila)

    async def stream_session_events(self, session_id, stop=None):
        fila = self.subscribe_session(session_id)
        try:
            while not (stop and stop.is_set()):
                try:
                    evento = await asyncio.wait_for(fila.get(), timeout=0.5)
                except TimeoutError:
                    continue
                yield evento
        finally:
            self.unsubscribe_session(session_id, fila)



async def test_run_bot_task_grava_no_feed_na_sessao_do_bot(tmp_path, isolated_env):
    orch, store, _client = _orquestrador(tmp_path)
    fake = _ClientResposta("capitulo 2 revisado")
    orch.client = fake  # type: ignore[assignment]
    try:
        resultado = await orch.run_bot_task("donna", "revisa o capitulo 2")

        assert resultado["ok"] is True
        assert resultado["text"] == "capitulo 2 revisado"
        # A sessao do JOB mora em `channel='job'`: nao aparece na lista de
        # sessoes da pessoa nem volta no proximo turno interativo.
        conversas = store.list_conversations(str(orch.directory), "donna", "job")
        assert conversas, "a conversa do job deveria existir no canal job"
        feed = store.list_bot_messages("donna")
        assert feed and feed[0]["title"].startswith("Donna Haraway")
        run = store.list_runs(kind="bot_task")
        assert run and run[0]["bot"] == "donna"
        assert run[0]["status"] == "completed"
        assert run[0]["trigger"] == "schedule"
        # O historico do job nao aparece na conversa interativa.
        assert store.list_conversations(str(orch.directory), "donna", "cli") == []
    finally:
        store.close()


async def test_run_bot_task_falha_vira_run_failed_e_mensagem(tmp_path, isolated_env):
    orch, store, _client = _orquestrador(tmp_path)
    fake = _ClientResposta(falhar=True)
    orch.client = fake  # type: ignore[assignment]
    try:
        resultado = await orch.run_bot_task("ada", "abre o PR")

        assert resultado["ok"] is False
        assert "provedor caiu" in resultado["error"]
        run = store.list_runs(kind="bot_task")[0]
        assert run["status"] == "failed"
        assert run["error_kind"] in {"network", "unknown"}
        feed = store.list_bot_messages("ada")
        assert feed and "falhou" in feed[0]["title"]
        assert "provedor caiu" in feed[0]["text"]
    finally:
        store.close()


async def test_run_bot_task_nega_permissao_sem_ninguem_para_aprovar(
    tmp_path, isolated_env
):
    """D7: segundo plano nao amplia permissao. Sem aprovador, nega e avisa."""
    orch, store, _client = _orquestrador(tmp_path)
    fake = _ClientResposta()
    fake.pedir_permissao = True
    orch.client = fake  # type: ignore[assignment]
    try:
        resultado = await orch.run_bot_task("donna", "compila a tese")

        assert fake.permissoes and fake.permissoes[0][1] == "reject"
        # Com texto junto da negacao, a resposta vale; sem texto, o erro diz
        # por que falhou.
        assert resultado["ok"] is (resultado["text"] != "")
    finally:
        store.close()


async def test_run_bot_task_recusa_bot_indisponivel(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    try:
        orch.bots["donna"] = Bot(
            **{**DEFAULT_BOTS["donna"].__dict__, "workspace": str(tmp_path / "nada")}
        )
        with pytest.raises(BotIndisponivel):
            await orch.run_bot_task("donna", "revisa")
        assert store.list_bot_messages("donna") == []
    finally:
        store.close()
        await client.aclose()


async def test_run_bot_task_recusa_bot_desconhecido(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    try:
        with pytest.raises(ValueError):
            await orch.run_bot_task("maria", "oi")
    finally:
        store.close()
        await client.aclose()


# ------------------------------------------------------------ ocupacao ---


async def test_bot_busy_olha_so_o_bot_ativo(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    try:
        orch._busy = True
        assert orch.bot_busy(orch.mode_key) is True
        assert orch.bot_busy("ada") is False
        orch._busy = False
        assert orch.bot_busy(orch.mode_key) is False
    finally:
        store.close()
        await client.aclose()


# ---------------------------------------------------------------- bus ---


def test_bus_recusa_bot_que_nao_esta_na_allowlist(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    bus = BotBus(orch)
    try:
        # `ariana` nao pode falar com `donna` (can_message vazio).
        resposta = asyncio.run(bus._ask("ariana", "donna", "revisa?", 0))
        assert "nao pode falar com donna" in resposta
        # E ninguem fala consigo mesmo.
        resposta = asyncio.run(bus._ask("gaia", "gaia", "oi", 0))
        assert "consigo mesmo" in resposta
        # Bot desconhecido nao vira silencio.
        resposta = asyncio.run(bus._ask("gaia", "maria", "oi", 0))
        assert "bot desconhecido" in resposta
    finally:
        store.close()
        asyncio.run(client.aclose())


def test_bus_para_na_profundidade_maxima(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    bus = BotBus(orch)

    async def _cenario() -> str:
        return await bus._ask("gaia", "donna", "revisa?", MAX_DEPTH)

    try:
        assert "profundidade maxima" in asyncio.run(_cenario())
    finally:
        store.close()
        asyncio.run(client.aclose())


def test_bus_repassa_a_chamada_e_audita(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    bus = BotBus(orch)
    chamadas: list[tuple[str, str]] = []

    async def run_bot_task(bot_id, prompt, **kwargs):
        chamadas.append((bot_id, prompt))
        return {"ok": True, "text": "capitulo 2 ok", "error": ""}

    orch.run_bot_task = run_bot_task  # type: ignore[assignment]

    async def _cenario() -> str:
        return await bus._ask("gaia", "donna", "status do capitulo 2?", 0)

    try:
        assert asyncio.run(_cenario()) == "capitulo 2 ok"
        assert chamadas and chamadas[0][0] == "donna"
        assert "Chamada de Gaia" in chamadas[0][1]
        feed = store.list_bot_messages("donna")
        assert feed and feed[0]["kind"] == "inter_bot"
        assert "pergunta de Gaia" in feed[0]["title"]
    finally:
        store.close()
        asyncio.run(client.aclose())


def test_bus_sem_loop_devolve_recusa_legivel(tmp_path, isolated_env):
    orch, store, client = _orquestrador(tmp_path)
    bus = BotBus(orch)
    try:
        # Sem `bind_loop` (fora do motor) a ferramenta nao trava: diz por que.
        assert "loop" in bus.ask_sync("gaia", "donna", "oi")
    finally:
        store.close()
        asyncio.run(client.aclose())
