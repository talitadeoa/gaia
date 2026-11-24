from __future__ import annotations

import asyncio
import contextlib
import logging
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import gaia.controller.orchestrator as orchestrator_module
from gaia.controller.briefing import BriefingMixin
from gaia.controller.career import CareerMixin
from gaia.controller.commands import CommandsMixin
from gaia.controller.contract import OrchestratorContract
from gaia.controller.goals import GoalTracker
from gaia.controller.initiative import InitiativeMixin
from gaia.controller.memory import MemoryMixin
from gaia.controller.orchestrator import Orchestrator
from gaia.controller.registration import RegistrationMixin
from gaia.controller.routines import RoutineMixin
from gaia.controller.speech import SpeechMixin
from gaia.controller.turns import TurnMixin
from gaia.controller.voice import VoiceMixin
from gaia.finance.watchlist import Watchlist
from gaia.memory.store import MemoryStore
from gaia.tasks.store import TaskStore


class FakeUI:
    """UI que so registra as chamadas (sem terminal real)."""

    def __init__(self) -> None:
        self.console = types.SimpleNamespace(width=80, print=lambda *a, **k: None)
        self.events: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.events.append((name, args, kwargs))
            if name == "confirm":
                return False
            if name == "permission":
                return "once"
            if name == "prompt":
                return ""
            return None

        return record

    def called(self, name: str) -> list[tuple[tuple, dict]]:
        return [(args, kwargs) for event, args, kwargs in self.events if event == name]


class FakeTTS:
    def __init__(self) -> None:
        self.enabled = True
        self.provider = "fake"
        self.spoken: list[str] = []

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        pass

    async def prepare(self, text: str):
        return None

    async def play_prepared(self, prepared) -> None:
        pass

    def set_provider(self, provider: str) -> None:
        self.provider = provider


class FakeIndex:
    def count(self) -> int:
        return 0

    def search(self, query: str, limit: int = 3):
        return []

    def stats(self) -> dict[str, int]:
        return {"notes": 0}

    def reindex(self, force: bool = False) -> dict[str, int]:
        return {"indexed": 0, "removed": 0, "total": 0}


class FakeBrain:
    def __init__(self, root: Path, preferences: str = "") -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.preferences_path = self.root / "preferences.md"
        self.preferences_path.write_text(preferences, encoding="utf-8")

    def load_preferences(self) -> str:
        return self.preferences_path.read_text(encoding="utf-8")

    def append_preference(self, text: str, *args) -> bool:
        with self.preferences_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n- {text}\n")
        return True


class FakeClient:
    def __init__(self) -> None:
        self.endpoint = "http://fake"
        self.model = "fake/model"
        self.aborted: list[str] = []

    async def health(self):
        return {}

    async def ensure_ready(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def abort(self, session_id: str) -> None:
        self.aborted.append(session_id)

    async def create_session(self, **kwargs):
        return types.SimpleNamespace(id="ses-fake")

    async def get_session(self, session_id: str):
        return None

    async def session_status(self, session_id: str):
        return {"type": "idle"}

    async def latest_assistant(self, session_id: str):
        return None


class FakeTranscriber:
    def warmup(self) -> None:
        pass

    def transcribe(self, audio, partial: bool = True) -> str:
        return ""


@pytest.fixture
def orch(tmp_path, cfg, monkeypatch):
    monkeypatch.setenv("GAIA_WATCHLIST_PATH", str(tmp_path / "watchlist.json"))
    monkeypatch.setenv("GAIA_GOALS_FILE", str(tmp_path / "goals.json"))
    cfg.focus.path = str(tmp_path / "focus.json")
    monkeypatch.setattr(
        orchestrator_module, "TaskStore", lambda: TaskStore(tmp_path / "tasks.json")
    )

    store = MemoryStore(tmp_path / "gaia.db")
    brain = FakeBrain(tmp_path / "brain")
    ui = FakeUI()
    tts = FakeTTS()
    client = FakeClient()
    instance = Orchestrator(
        cfg=cfg,
        logger=logging.getLogger("test.gaia"),
        ui=ui,
        store=store,
        index=FakeIndex(),
        brain=brain,
        tts=tts,
        transcriber=FakeTranscriber(),
        recorder=types.SimpleNamespace(),
        client=client,
    )
    instance.watchlist = Watchlist(tmp_path / "watchlist.json")
    instance.goals = GoalTracker(tmp_path / "goals.json")
    yield instance
    store.close()


def test_mixins_share_the_explicit_contract():
    mixins = (
        SpeechMixin,
        TurnMixin,
        VoiceMixin,
        CommandsMixin,
        MemoryMixin,
        BriefingMixin,
        CareerMixin,
        RegistrationMixin,
        RoutineMixin,
        InitiativeMixin,
    )
    for mixin in mixins:
        assert issubclass(mixin, OrchestratorContract)
    assert issubclass(Orchestrator, OrchestratorContract)


def test_contract_is_abstract_and_orchestrator_concrete():
    with pytest.raises(TypeError):
        OrchestratorContract()
    assert not Orchestrator.__abstractmethods__


def test_state_is_initialized(orch):
    assert orch.conversation_id is None
    assert orch.session_id == ""
    assert orch.cfg.interface.fast_smalltalk is True
    assert orch._voice_mode == bool(orch.cfg.audio.continuous)


def test_quick_reply_respects_fast_smalltalk(orch):
    orch.cfg.interface.fast_smalltalk = True
    reply = orch._quick_reply("oi")
    assert reply

    orch.cfg.interface.fast_smalltalk = False
    assert orch._quick_reply("oi") is None


def test_quick_reply_ignores_real_requests(orch):
    orch.cfg.interface.fast_smalltalk = True
    assert orch._quick_reply("revise a arquitetura do projeto") is None


def test_is_short_prompt(orch):
    assert orch._is_short_prompt("ok") is True
    assert orch._is_short_prompt("uma duas tres quatro cinco seis sete oito nove dez onze") is False
    assert orch._is_short_prompt("linha\noutra") is False


@pytest.mark.asyncio
async def test_run_turn_repassa_a_mensagem_original(orch, monkeypatch):
    captured: dict[str, object] = {}

    async def fake_turn(text, system=None, session_id=None, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(orch, "_turn_impl", fake_turn)

    await orch._run_turn("prompt com contexto", system="s", history_text="pergunta")

    assert captured["text"] == "prompt com contexto"
    assert captured["history_text"] == "pergunta"


@pytest.mark.asyncio
async def test_prompt_dinamico_vai_na_mensagem_nao_no_system(orch, monkeypatch):
    orch._prepare_conversation()
    captured: dict[str, object] = {}

    async def fake_turn(text, system=None, session_id=None, **kwargs):
        captured["text"] = text
        captured["system"] = system
        captured.update(kwargs)
        return ""

    monkeypatch.setattr(orch, "_run_turn", fake_turn)

    await orch._handle_task_impl("qual a capital da França?", speak=False)

    assert "[Agora:" not in captured["system"]
    assert "[Agora:" in captured["text"]
    assert captured["history_text"] == "qual a capital da França?"
    assert "Diretriz da Gaia" in captured["system"]


@pytest.mark.asyncio
async def test_dispatch_silent_answers_smalltalk_locally(orch):
    orch._prepare_conversation()
    answer = await orch._dispatch_silent("oi")

    assert answer
    turns = orch.store.turns(orch.conversation_id)
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["text"] == answer


@pytest.mark.asyncio
async def test_chines_troca_o_idioma_do_eco(orch):
    """`/chines en|fr` escolhe o idioma e liga o eco; `on|off` liga/desliga."""
    assert orch.cfg.interface.chinese_echo is False

    await orch._handle_command("/chines en")
    assert orch.cfg.interface.chinese_echo is True
    assert orch.cfg.interface.echo_language == "en"

    await orch._handle_command("/chines off")
    assert orch.cfg.interface.chinese_echo is False
    assert orch.cfg.interface.echo_language == "en"

    await orch._handle_command("/chines")
    assert orch.cfg.interface.chinese_echo is True

    await orch._handle_command("/chines fr")
    assert orch.cfg.interface.echo_language == "fr"
    assert orch.ui.called("success")


def test_rotina_continua_pausada_no_motor_de_verdade(orch):
    """O gate roda no Orchestrator real (nao so no stub do mixin)."""
    assert orch._routine_on("initiative:agenda") is True
    orch.store.set_routine("initiative:agenda", enabled=False)
    assert orch._routine_on("initiative:agenda") is False
    # Os gates de config continuam valendo por cima do checkbox.
    orch.cfg.agenda.enabled = False
    orch.store.set_routine("initiative:agenda", enabled=True)
    assert orch._routine_on("initiative:agenda") is False


@pytest.mark.asyncio
async def test_rodar_rotina_a_mao_no_motor_de_verdade(orch, monkeypatch):
    """`run_routine_now` grava o run e chama o handler de verdade."""
    chamadas: list[str] = []

    async def fake_consolidar_anterior():
        chamadas.append("dia anterior")

    monkeypatch.setattr(orch, "_consolidate_previous_day", fake_consolidar_anterior)

    resultado = await orch.run_routine_now("memory:previous_day")

    assert chamadas == ["dia anterior"]
    assert resultado["status"] == "completed"
    assert resultado["trigger"] == "manual"
    ultima = orch.store.last_run("memory", "memory:previous_day")
    assert ultima is not None and ultima["trigger"] == "manual"
    # A rotina aparece no painel com o run manual como ultima execucao.
    from gaia.control import routines as routines_mod

    item = routines_mod.render(
        routines_mod.find("memory:previous_day"), orch.cfg, orch.store
    )
    assert item["last_run"]["status"] == "completed"
    assert item["status"] == "active"


def test_eco_key_cai_no_chines_quando_a_config_e_estranha(orch):
    orch.cfg.interface.echo_language = "klingon"
    assert orch._echo_key == "zh"


@pytest.mark.asyncio
async def test_translate_echo_usa_prompt_e_cache_por_idioma(orch):
    """Cada idioma tem o proprio prompt, a propria sessao e o proprio cache."""
    pedidos: list[tuple[str, str]] = []

    class _Client(FakeClient):
        async def create_session(self, **kwargs):
            return types.SimpleNamespace(id=f"ses-{kwargs.get('title', '')}")

        async def prompt_sync(self, session_id, prompt, system=None, **kwargs):
            pedidos.append((session_id, system or ""))
            return types.SimpleNamespace(text=f"  traduzido:  {prompt}  ")

    orch.client = _Client()

    ingles = await orch.translate_echo("bom dia", "en")
    assert ingles.startswith("traduzido: ") and ingles.endswith("bom dia")
    assert "English" in pedidos[0][1]

    # Cache: a segunda chamada nao vai ao modelo.
    assert await orch.translate_echo("bom dia", "en") == ingles
    assert len(pedidos) == 1

    frances = await orch.translate_echo("bom dia", "fr")
    assert frances != ingles
    assert "français" in pedidos[1][1]
    assert pedidos[0][0] != pedidos[1][0]

    # Sem idioma, segue o escolhido na config.
    orch.cfg.interface.echo_language = "zh"
    await orch.translate_echo("bom dia")
    assert "chinês" in pedidos[2][1]


@pytest.mark.asyncio
async def test_translate_echo_nao_derruba_o_turno(orch):
    class _Quebrado(FakeClient):
        async def prompt_sync(self, *args, **kwargs):
            raise RuntimeError("sem rede")

    orch.client = _Quebrado()
    assert await orch.translate_echo("oi", "en") == ""


@pytest.mark.asyncio
async def test_toggle_commands_update_config(orch):
    assert await orch._handle_command("/sair") is True

    await orch._handle_command("/rapido on")
    assert orch.cfg.interface.fast is True

    await orch._handle_command("/contexto off")
    assert orch.cfg.secondbrain.auto_context is False

    await orch._handle_command("/confirmar on")
    assert orch.cfg.secondbrain.confirm_create is True

    await orch._handle_command("/texto")
    assert orch.cfg.audio.continuous is False
    assert orch.tts.enabled is False

    await orch._handle_command("/fala")
    assert orch.cfg.audio.continuous is True
    assert orch.tts.enabled is True

    await orch._handle_command("/voz off")
    assert orch.tts.enabled is False


@pytest.mark.asyncio
async def test_pensando_alterna_o_raciocinio_na_tela(orch):
    orch.cfg.interface.show_reasoning = True

    await orch._handle_command("/pensando off")
    assert orch.cfg.interface.show_reasoning is False

    await orch._handle_command("/pensando on")
    assert orch.cfg.interface.show_reasoning is True

    # Sem argumento, alterna (e nao mexe no interruptor do Telegram).
    orch.cfg.telegram.show_reasoning = False
    await orch._handle_command("/pensando")
    assert orch.cfg.interface.show_reasoning is False
    assert orch.cfg.telegram.show_reasoning is False
    assert orch.ui.called("success")


def test_voltar_ao_texto_para_escuta_e_desliga_voz(orch):
    orch.cfg.audio.continuous = True
    orch._voice_mode = True
    orch.tts.enabled = True

    orch._return_to_text("voltando ao texto")

    assert orch.cfg.audio.continuous is False
    assert orch._voice_mode is False
    assert orch.tts.enabled is False
    assert orch.ui.called("info")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/novo", "/nova"])
async def test_novo_e_nova_abrem_sessao_nova(orch, command):
    antes = orch.session_id
    assert await orch._handle_command(command) is False
    assert orch.session_id != antes
    assert orch.ui.called("success")


@pytest.mark.asyncio
async def test_prepare_liga_o_worker_de_memoria(orch, monkeypatch):
    chamadas: list[bool] = []

    async def fake_bootstrap() -> None:
        orch.session_id = "ses_teste"

    monkeypatch.setattr(orch, "_bootstrap", fake_bootstrap)
    monkeypatch.setattr(orch, "_start_memory_worker", lambda: chamadas.append(True))

    await orch.prepare()

    assert chamadas == [True]


@pytest.mark.asyncio
async def test_comando_consolidar_usa_o_dia_pedido(orch, monkeypatch):
    chamadas: list[tuple] = []

    async def fake(day=None, *, force=False, timeout_seconds=None):
        chamadas.append((day, force))
        return "Memoria/Memória teste.md"

    monkeypatch.setattr(orch, "consolidate_day", fake)

    await orch._handle_command("/consolidar ontem forcar")

    esperado = datetime.now().astimezone().date() - timedelta(days=1)
    assert chamadas == [(esperado, True)]
    assert orch.ui.called("success")


@pytest.mark.asyncio
async def test_comando_sonhar_usa_o_dia_pedido_e_consolida(orch, monkeypatch):
    consolidados: list = []
    sonhados: list[tuple] = []

    async def fake_consolidar(day=None, *, force=False, timeout_seconds=None):
        consolidados.append(day)
        return None

    async def fake_sonhar(day=None, *, force=False, timeout_seconds=None):
        sonhados.append((day, force))
        return "Memoria/Sonho teste.md"

    monkeypatch.setattr(orch, "consolidate_day", fake_consolidar)
    monkeypatch.setattr(orch, "dream_day", fake_sonhar)

    await orch._handle_command("/sonhar ontem forcar")

    esperado = datetime.now().astimezone().date() - timedelta(days=1)
    assert consolidados == [esperado]
    assert sonhados == [(esperado, True)]
    assert orch.ui.called("success")


@pytest.mark.asyncio
async def test_sonho_so_acontece_depois_do_horario(orch, monkeypatch):
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_at = "02:00"
    chamadas: list = []

    async def fake_consolidar(day=None, *, force=False, timeout_seconds=None):
        return None

    async def fake_sonhar(day=None, *, force=False, timeout_seconds=None):
        chamadas.append(day)
        return "Memoria/Sonho teste.md"

    monkeypatch.setattr(orch, "consolidate_day", fake_consolidar)
    monkeypatch.setattr(orch, "dream_day", fake_sonhar)

    manha = datetime.now().astimezone().replace(hour=9, minute=0)
    await orch._maybe_dream(manha)
    assert chamadas == []

    madrugada = datetime.now().astimezone().replace(hour=3, minute=0)
    await orch._maybe_dream(madrugada)
    # De madrugada o sonho e sobre o dia que acabou (ontem).
    assert chamadas == [madrugada.date() - timedelta(days=1)]


@pytest.mark.asyncio
async def test_sonho_da_noite_sonha_sobre_hoje(orch, monkeypatch):
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_at = "23:30"
    orch.cfg.memory.dream_until = "08:00"
    chamadas: list = []

    async def fake_consolidar(day=None, *, force=False, timeout_seconds=None):
        return None

    async def fake_sonhar(day=None, *, force=False, timeout_seconds=None):
        chamadas.append(day)
        return "Memoria/Sonho teste.md"

    monkeypatch.setattr(orch, "consolidate_day", fake_consolidar)
    monkeypatch.setattr(orch, "dream_day", fake_sonhar)

    noite = datetime.now().astimezone().replace(hour=23, minute=45)
    await orch._maybe_dream(noite)
    assert chamadas == [noite.date()]


@pytest.mark.asyncio
async def test_sonho_desabilitado_nao_sonha(orch, monkeypatch):
    orch.cfg.memory.dream_enabled = False
    chamadas: list = []

    async def fake_sonhar(day=None, *, force=False, timeout_seconds=None):
        chamadas.append(day)
        return None

    monkeypatch.setattr(orch, "dream_day", fake_sonhar)

    await orch._maybe_dream(datetime.now().astimezone().replace(hour=3, minute=0))
    assert chamadas == []


@pytest.mark.asyncio
async def test_auto_approve_warns(orch):
    await orch._handle_command("/autoaprovar on")
    assert orch.cfg.agent.auto_approve is True
    assert orch.ui.called("warning")


@pytest.mark.asyncio
async def test_unknown_command_warns(orch):
    assert await orch._handle_command("/inexistente") is False
    warnings = orch.ui.called("warning")
    assert warnings and "desconhecido" in warnings[-1][0][0]


@pytest.mark.asyncio
async def test_mode_command_without_argument_lists_modes(orch):
    await orch._handle_command("/modo")
    infos = orch.ui.called("info")
    assert any("modo atual" in args[0] for args, _ in infos)


@pytest.mark.asyncio
async def test_switching_to_unknown_mode_warns(orch):
    await orch._switch_mode("nao-existe")
    warnings = orch.ui.called("warning")
    assert warnings and "modo desconhecido" in warnings[-1][0][0]


@pytest.mark.asyncio
async def test_status_reports_state(orch):
    await orch._handle_command("/status")
    assert orch.ui.called("status")


@pytest.mark.asyncio
async def test_history_empty_conversation_informs(orch):
    orch._prepare_conversation()
    await orch._handle_command("/historico")
    infos = orch.ui.called("info")
    assert any("sem histórico" in args[0] for args, _ in infos)


def test_preferences_block_reads_brain(orch):
    orch.brain.preferences_path.write_text("Gosta de respostas curtas.", encoding="utf-8")
    assert "respostas curtas" in orch._preferences_block()


def test_user_name_from_preferences(orch):
    orch.brain.preferences_path.write_text("Nome preferido: Talita.", encoding="utf-8")
    assert orch._user_name() == "Talita"


def test_local_greeting_uses_name(orch):
    orch.brain.preferences_path.write_text("Nome preferido: Talita.", encoding="utf-8")
    greeting = orch._local_greeting(__import__("datetime").datetime.now().astimezone())
    assert "Talita" in greeting


def test_idle_message_does_not_repeat(orch):
    first = orch._idle_message()
    second = orch._idle_message()
    assert first and second
    assert first != second


def test_find_task_matches_id_and_title(orch):
    task = orch.tasks.upsert("Revisar o README")
    assert orch._find_task(task.id) is task
    assert orch._find_task("readme") is task
    assert orch._find_task("inexistente") is None


class RecordingClient(FakeClient):
    """Cria sessoes com atraso para expor intercalacoes entre trocas de modo."""

    def __init__(self) -> None:
        super().__init__()
        self.created: dict[str, str] = {}

    async def create_session(self, title: str | None = None, **kwargs):
        await asyncio.sleep(0.01)
        session_id = f"ses-{len(self.created) + 1}"
        self.created[session_id] = title or ""
        return types.SimpleNamespace(id=session_id)


@pytest.mark.asyncio
async def test_trocas_concorrentes_de_modo_nao_cruzam_sessoes(orch):
    client = RecordingClient()
    orch.client = client

    await asyncio.gather(orch.select_mode("trabalhadora"), orch.select_mode("pesquisadora"))

    trabalhadora = orch.store.get_or_create_conversation(str(orch.directory), "trabalhadora")
    pesquisadora = orch.store.get_or_create_conversation(str(orch.directory), "pesquisadora")
    sid_trabalhadora = trabalhadora["agent_session_id"]
    sid_pesquisadora = pesquisadora["agent_session_id"]

    assert sid_trabalhadora and sid_pesquisadora
    assert sid_trabalhadora != sid_pesquisadora
    assert "Trabalhadora" in client.created[sid_trabalhadora]
    assert "Pesquisadora" in client.created[sid_pesquisadora]

    atual = orch.store.get_conversation(orch.conversation_id)
    assert atual["mode"] == orch.persona.key
    assert atual["agent_session_id"] == orch.session_id


class ForeignDirectoryClient(FakeClient):
    """Sessao viva, mas de outro diretorio (a API v2 responde 200 mesmo assim)."""

    async def get_session(self, session_id: str):
        return types.SimpleNamespace(id=session_id, directory="/Users/talita/zora")

    async def create_session(self, title: str | None = None, **kwargs):
        return types.SimpleNamespace(id="ses-nova")


@pytest.mark.asyncio
async def test_sessao_de_outro_diretorio_nao_e_retomada(orch):
    orch._prepare_conversation()
    conversation_id = orch.conversation_id
    orch.store.add_turn(conversation_id, "user", "oi, tudo bem?")
    orch.store.set_session(conversation_id, "ses_de_zora")

    orch.client = ForeignDirectoryClient()
    await orch._activate_conversation(conversation_id, orch.persona)

    assert orch.session_id == "ses-nova"
    conversa = orch.store.get_conversation(conversation_id)
    assert conversa["agent_session_id"] == "ses-nova"
    assert "Talita: oi, tudo bem?" in orch._take_resume_context()


@pytest.mark.asyncio
async def test_sessao_ausente_reinjeta_o_historico_local(orch):
    orch._prepare_conversation()
    conversation_id = orch.conversation_id
    orch.store.add_turn(conversation_id, "user", "quero vaga de suporte")
    orch.store.add_turn(conversation_id, "assistant", "vamos revisar seu curriculo")
    orch.store.set_session(conversation_id, "ses_sumiu")

    await orch._activate_conversation(conversation_id, orch.persona)

    conversa = orch.store.get_conversation(conversation_id)
    assert conversa["agent_session_id"] != "ses_sumiu"
    contexto = orch._take_resume_context()
    assert "Talita: quero vaga de suporte" in contexto
    assert "Gaia: vamos revisar seu curriculo" in contexto
    assert orch._take_resume_context() == ""


class FakeAgendaBriefing:
    enabled = True

    def __init__(self, notes: list[dict], routines: list[dict] | None = None) -> None:
        self.notes = notes
        self.rotinas = routines or []

    async def notes_on(self, date: str) -> list[dict]:
        return self.notes

    async def routines(self) -> list[dict]:
        return self.rotinas


class FakeTelegram:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> None:
        self.sent.append(text)


class FalhaUmaVezTelegram(FakeTelegram):
    """Falha na primeira entrega e funciona depois (Telegram fora do ar)."""

    def __init__(self) -> None:
        super().__init__()
        self.falhas = 1

    async def send_message(self, text: str) -> None:
        if self.falhas:
            self.falhas -= 1
            raise RuntimeError("Telegram fora do ar")
        await super().send_message(text)


def _notas_briefing() -> list[dict]:
    return [
        {
            "id": "t1",
            "text": "Responder a Stefanini",
            "is_checkbox": True,
            "completed": False,
            "parent_id": None,
            "routine_id": None,
            "order": 1,
        },
        {
            "id": "r",
            "text": "Rotina matinal",
            "is_checkbox": True,
            "completed": False,
            "parent_id": None,
            "routine_id": "rot1",
            "order": 2,
        },
        {
            "id": "r1",
            "text": "alongar",
            "is_checkbox": True,
            "completed": True,
            "parent_id": "r",
            "routine_id": "rot1",
            "order": 1,
        },
        {
            "id": "r2",
            "text": "meditar",
            "is_checkbox": True,
            "completed": False,
            "parent_id": "r",
            "routine_id": "rot1",
            "order": 2,
        },
    ]


@pytest.mark.asyncio
async def test_briefing_envia_uma_vez_por_dia(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.cfg.briefing.morning_routine = "matinal"
    dia = datetime.now().astimezone().date()

    assert await orch._send_briefing("morning", dia) is True
    assert len(orch.telegram.sent) == 1
    mensagem = orch.telegram.sent[0]
    assert "Bom dia" in mensagem
    assert "Responder a Stefanini" in mensagem
    assert "Rotina matinal" not in mensagem
    assert "alongar" not in mensagem

    assert await orch._send_briefing("morning", dia) is False
    assert len(orch.telegram.sent) == 1


@pytest.mark.asyncio
async def test_briefing_do_meio_dia_mostra_pendentes(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    dia = datetime.now().astimezone().date()

    assert await orch._send_briefing("noon", dia) is True
    mensagem = orch.telegram.sent[0]
    assert "Feito até agora" in mensagem
    assert "Ainda falta" in mensagem
    assert "☐ Responder a Stefanini" in mensagem
    assert "Rotina matinal" not in mensagem
    assert "alongar" not in mensagem


@pytest.mark.asyncio
async def test_career_envia_uma_vez_por_dia_e_grava_o_estado(orch, monkeypatch):
    from gaia.career.models import Job
    from gaia.career.service import load_state

    jobs = [
        Job(
            title="Analista de Suporte N1",
            company="Stefanini",
            url="https://exemplo.test/1",
            source="Remotive",
            location="Brazil",
            salary="R$ 2.800",
            seniority="Júnior",
            description="troubleshooting, windows e redes",
            remote=True,
        )
    ]

    async def fake_fetch_all(sources=()):
        return jobs, []

    monkeypatch.setattr("gaia.career.service.fetch_all", fake_fetch_all)
    orch.telegram = FakeTelegram()
    dia = datetime.now().astimezone().date()

    assert await orch._send_career(dia) is True
    assert len(orch.telegram.sent) == 1
    assert "Vagas novas" in orch.telegram.sent[0]
    assert "Stefanini" in orch.telegram.sent[0]

    state = load_state(orch._career_state_path())
    assert state["vagas"][0]["empresa"] == "Stefanini"
    assert state["vagas"][0]["status"] == "nova"

    # Segundo disparo no mesmo dia nao busca nem envia de novo.
    assert await orch._send_career(dia) is False
    assert len(orch.telegram.sent) == 1


@pytest.mark.asyncio
async def test_career_deduplica_na_segunda_rodada(orch, monkeypatch):
    from gaia.career.models import Job
    from gaia.career.service import load_state

    jobs = [
        Job(
            title="Técnico de Suporte",
            company="Acme",
            url="https://exemplo.test/a",
            source="Remotive",
            location="Brazil",
            remote=True,
        )
    ]

    async def fake_fetch_all(sources=()):
        return jobs, []

    monkeypatch.setattr("gaia.career.service.fetch_all", fake_fetch_all)
    orch.telegram = FakeTelegram()
    dia = datetime.now().astimezone().date()

    assert await orch._send_career(dia) is True
    state = load_state(orch._career_state_path())
    assert len(state["vagas"]) == 1

    # Rodada seguinte (outro dia) ve a mesma vaga: nao registra de novo.
    outra = dia + timedelta(days=1)
    orch._career_last_attempt = 0.0
    assert await orch._send_career(outra) is True
    state = load_state(orch._career_state_path())
    assert len(state["vagas"]) == 1
    assert "Sem vagas novas" in orch.telegram.sent[-1]


@pytest.mark.asyncio
async def test_career_nao_roda_sem_telegram(orch):
    orch.telegram = types.SimpleNamespace(enabled=False)

    orch._start_career_worker()

    assert orch._career_task is None


@pytest.mark.asyncio
async def test_start_career_liga_o_agendador_sem_bootstrap(orch):
    """O `telegram serve` chama start_career/start_briefing no boot.

    Desde a fase C a hora nao e decidida em cada worker: start_career liga o
    agendador unico, que le a tabela ``automations``.
    """
    orch.telegram = FakeTelegram()

    orch.start_career()

    assert orch._scheduler_task is not None
    # Idempotente: chamar de novo nao duplica a tarefa.
    task = orch._scheduler_task
    orch.start_career()
    assert orch._scheduler_task is task
    orch._scheduler_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await orch._scheduler_task
    orch._scheduler_task = None


@pytest.mark.asyncio
async def test_career_reenvia_o_que_o_telegram_nao_entregou(orch, monkeypatch):
    from gaia.career.models import Job
    from gaia.career.service import load_state, pending_jobs

    jobs = [
        Job(
            title="Analista de Suporte N1",
            company="Stefanini",
            url="https://exemplo.test/1",
            source="Remotive",
            location="Brazil",
            remote=True,
        )
    ]

    async def fake_fetch_all(sources=()):
        return jobs, []

    monkeypatch.setattr("gaia.career.service.fetch_all", fake_fetch_all)
    orch.telegram = FalhaUmaVezTelegram()
    dia = datetime.now().astimezone().date()

    # Primeira tentativa: a vaga e gravada e continua pendente.
    assert await orch._send_career(dia) is False
    state = load_state(orch._career_state_path())
    assert len(state["vagas"]) == 1
    assert len(pending_jobs(state)) == 1
    assert state["vagas"][0]["status"] == "nova"

    # Segunda tentativa no mesmo dia: entrega sem precisar buscar vaga nova.
    orch._career_last_attempt = 0.0
    assert await orch._send_career(dia) is True
    assert len(orch.telegram.sent) == 1
    assert "Stefanini" in orch.telegram.sent[0]

    state = load_state(orch._career_state_path())
    assert pending_jobs(state) == []
    assert state["vagas"][0]["entrega"] == "entregue"
    assert state["vagas"][0]["entregue_em"]


def test_career_due_respeita_config(orch):
    from datetime import date as _date

    from gaia.career.service import career_due

    cfg = orch.cfg.career
    dia = _date(2026, 11, 9)  # segunda

    assert career_due(cfg.at, cfg.at, cfg.until, dia.weekday(), cfg.days) is True
    assert career_due(cfg.until, cfg.at, cfg.until, dia.weekday(), cfg.days) is False


class InsightClient(FakeClient):
    """Cliente que devolve um insight pronto (sem rede)."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def prompt_sync(self, session_id, text, **kwargs):
        self.prompts.append(text)
        return types.SimpleNamespace(text="Você fechou o que era trabalho e deixou a decisão pra depois.")

    async def delete_session(self, session_id) -> None:
        pass


@pytest.mark.asyncio
async def test_fecho_da_noite_manda_feito_nao_foi_e_insight(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.client = InsightClient()
    orch.cfg.briefing.morning_routine = "matinal"
    dia = datetime.now().astimezone().date()

    assert await orch._send_evening_report(dia) is True
    assert len(orch.telegram.sent) == 1
    mensagem = orch.telegram.sent[0]
    assert mensagem.startswith("Boa noite")
    assert "Feito hoje" in mensagem
    assert "☑ alongar" in mensagem
    assert "Não foi" in mensagem
    assert "☐ Responder a Stefanini" in mensagem
    assert "☐ meditar" in mensagem
    assert "Você fechou o que era trabalho" in mensagem

    # o prompt levou o recap do dia
    assert "Tarefas que não foram:" in orch.client.prompts[0]
    # a fake de Agenda não expõe metas: fica de fora do recap, sem quebrar
    assert "Metas ativas" not in orch.client.prompts[0]

    # uma vez por dia: de novo, não manda
    assert await orch._send_evening_report(dia) is False
    assert len(orch.telegram.sent) == 1


@pytest.mark.asyncio
async def test_fecho_da_noite_sem_insight_quando_o_modelo_falha(orch):
    class SemInsight(InsightClient):
        async def prompt_sync(self, session_id, text, **kwargs):
            raise RuntimeError("provedor fora do ar")

    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.client = SemInsight()
    dia = datetime.now().astimezone().date()

    assert await orch._send_evening_report(dia) is True
    mensagem = orch.telegram.sent[0]
    assert "Feito hoje" in mensagem
    assert "Você fechou" not in mensagem


# ------------------------------------------------------------------- modelos
def _catalogo(*slugs: str, current: str = "deepseek/deepseek-flash"):
    from gaia.agent.catalog import ModelCatalog, ModelOption

    options = []
    for index, slug in enumerate(slugs):
        provider, _, model = slug.partition("/")
        options.append(
            ModelOption(provider, model, current=slug == current or (index == 0 and not current))
        )
    return ModelCatalog(options=options)


@pytest.mark.asyncio
async def test_modelo_sem_argumento_lista_numerada(orch, monkeypatch):
    import gaia.agent.catalog as catalog_module

    async def fake(cfg, *, client=None, timeout=6.0):
        return _catalogo("deepseek/deepseek-flash", "deepseek/deepseek-chat")

    monkeypatch.setattr(catalog_module, "collect_models", fake)

    responses = await orch.model_messages("")

    texts = [text for _kind, text in responses]
    assert " 1. ● deepseek/deepseek-flash" in texts
    assert " 2. ○ deepseek/deepseek-chat" in texts
    assert any("use /modelo <número>" in text for text in texts)


@pytest.mark.asyncio
async def test_comando_modelo_imprime_a_lista_no_terminal(orch, monkeypatch):
    import gaia.agent.catalog as catalog_module

    async def fake(cfg, *, client=None, timeout=6.0):
        return _catalogo("deepseek/deepseek-flash")

    monkeypatch.setattr(catalog_module, "collect_models", fake)

    await orch._handle_command("/modelo")

    infos = [args[0] for args, _kwargs in orch.ui.called("info")]
    assert any("deepseek/deepseek-flash" in text for text in infos)


@pytest.mark.asyncio
async def test_modelo_por_numero_troca_e_persiste(orch, monkeypatch, tmp_path):
    import gaia.agent.catalog as catalog_module

    async def fake(cfg, *, client=None, timeout=6.0):
        return _catalogo("deepseek/deepseek-flash", "openai/gpt-5")

    monkeypatch.setattr(catalog_module, "collect_models", fake)
    alvo = tmp_path / "config.toml"
    alvo.write_text('[agent]\nprovider = "deepseek"\nmodel = "deepseek-flash"\n', encoding="utf-8")
    orch.cfg.config_file = str(alvo)

    responses = await orch.model_messages("2")

    assert orch.cfg.agent.provider == "openai"
    assert orch.cfg.agent.model == "gpt-5"
    assert any("modelo da próxima mensagem: openai/gpt-5" in text for _k, text in responses)
    assert "openai" in alvo.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_modelo_por_numero_fora_da_lista_avisa(orch, monkeypatch):
    import gaia.agent.catalog as catalog_module

    async def fake(cfg, *, client=None, timeout=6.0):
        return _catalogo("deepseek/deepseek-flash")

    monkeypatch.setattr(catalog_module, "collect_models", fake)

    responses = await orch.model_messages("9")

    assert responses[0][0] == "warning"
    assert "fora da lista" in responses[0][1]
    assert orch.cfg.agent.model == "deepseek/deepseek-v4.1-flash"


@pytest.mark.asyncio
async def test_modelo_por_slug_troca_sem_consultar_catalogo(orch, monkeypatch, tmp_path):
    import gaia.agent.catalog as catalog_module

    def explode(*args, **kwargs):  # pragma: no cover - so falha se for chamado
        raise AssertionError("slug explícito não deve listar provedores")

    monkeypatch.setattr(catalog_module, "collect_models", explode)
    orch.cfg.config_file = str(tmp_path / "config.toml")

    responses = await orch.model_messages("anthropic/claude-x")

    assert orch.cfg.agent.provider == "anthropic"
    assert orch.cfg.agent.model == "claude-x"
    assert any("claude-x" in text for _k, text in responses)
    assert any(kind == "info" and "salvo em" in text for kind, text in responses)


@pytest.mark.asyncio
async def test_modelo_sem_barra_resolve_provedor_pela_lista(orch, monkeypatch):
    import gaia.agent.catalog as catalog_module

    async def fake(cfg, *, client=None, timeout=6.0):
        return _catalogo("deepseek/deepseek-flash", "openai/gpt-5")

    monkeypatch.setattr(catalog_module, "collect_models", fake)

    await orch.model_messages("gpt-5")

    assert orch.cfg.agent.provider == "openai"
    assert orch.cfg.agent.model == "gpt-5"


@pytest.mark.asyncio
async def test_modelo_id_desconhecido_cai_no_provedor_atual(orch, monkeypatch):
    import gaia.agent.catalog as catalog_module

    async def fake(cfg, *, client=None, timeout=6.0):
        return _catalogo("deepseek/deepseek-flash")

    monkeypatch.setattr(catalog_module, "collect_models", fake)
    orch.cfg.config_file = ""

    await orch.model_messages("modelo-novo")

    assert orch.cfg.agent.provider == "commandcode"
    assert orch.cfg.agent.model == "modelo-novo"


@pytest.mark.asyncio
async def test_modelo_slug_invalido_vira_erro(orch):
    responses = await orch.model_messages("sem-modelo/")

    assert responses[0][0] == "error"
    assert orch.cfg.agent.model == "deepseek/deepseek-v4.1-flash"


@pytest.mark.asyncio
async def test_modelo_reinicia_provedor_em_cache(orch, tmp_path):
    chamadas: list[bool] = []

    async def reset():
        chamadas.append(True)

    orch.client.reset_provider = reset
    orch.cfg.config_file = str(tmp_path / "config.toml")

    await orch.model_messages("deepseek/deepseek-chat")

    assert chamadas == [True]


class FakeTelegram:
    """Cliente do Telegram que só registra o que foi enviado."""

    enabled = True

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send_message(self, text: str, chat_id=None):
        if self.fail:
            raise RuntimeError("telegram fora do ar")
        self.sent.append(text)
        return {"message_id": len(self.sent)}


@pytest.mark.asyncio
async def test_proativo_no_canal_telegram_vai_para_o_chat(orch):
    telegram = FakeTelegram()
    orch.telegram = telegram
    orch.channel = "telegram"

    await orch._say_proactive("Lembrete: beber água")

    assert telegram.sent == ["Lembrete: beber água"]
    assert orch.tts.spoken == []


@pytest.mark.asyncio
async def test_proativo_no_canal_cli_continua_falando_local(orch):
    telegram = FakeTelegram()
    orch.telegram = telegram
    orch.channel = "cli"

    await orch._say_proactive("Bom dia")

    assert telegram.sent == []
    assert orch.tts.spoken == ["Bom dia"]


@pytest.mark.asyncio
async def test_proativo_no_serve_dono_do_telegram_vai_para_o_chat(orch):
    telegram = FakeTelegram()
    orch.telegram = telegram
    orch.channel = "serve"
    orch.owns_telegram = True

    await orch._say_proactive("Bom dia")

    assert telegram.sent == ["Bom dia"]
    assert orch.tts.spoken == []


@pytest.mark.asyncio
async def test_proativo_no_serve_sem_poller_nao_toca_audio(orch):
    orch.channel = "serve"
    orch.owns_telegram = False

    await orch._say_proactive("Bom dia")

    assert orch.tts.spoken == []


@pytest.mark.asyncio
async def test_proativo_nao_quebra_quando_o_telegram_falha(orch):
    orch.telegram = FakeTelegram(fail=True)
    orch.channel = "telegram"

    await orch._say_proactive("qualquer coisa")

    assert orch.ui.called("proactive")


@pytest.mark.asyncio
async def test_speak_nao_toca_audio_local_no_canal_telegram(orch):
    orch.channel = "telegram"

    await orch._speak("Combinado. Vou te lembrar.")

    assert orch.tts.spoken == []


@pytest.mark.asyncio
async def test_speak_nao_toca_audio_local_no_serve(orch):
    """O `serve` roda headless no mini: audio local ali e silencio."""
    orch.channel = "serve"

    await orch._speak("Combinado. Vou te lembrar.")

    assert orch.tts.spoken == []


@pytest.mark.asyncio
async def test_aviso_do_sonho_sai_uma_vez_no_dia_seguinte(orch, monkeypatch):
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_notify = True
    orch.cfg.memory.dream_notify_at = "08:00"
    orch.cfg.proactive.quiet_hours = "23:00-08:00"
    ontem = datetime.now().astimezone().date() - timedelta(days=1)
    orch.store.set_meta(f"memory_dreamed:{ontem.isoformat()}", "Sonhos/Sonho x.md")
    nota = types.SimpleNamespace(
        rel_path="Sonhos/Sonho x.md",
        title="Sonho " + ontem.isoformat(),
        body=(
            "A madrugada costura fios soltos.\n\n"
            "## Pergunta para amanhã\nO que fica?"
        ),
        tags=["sonho"],
        links=[],
    )
    orch.brain = types.SimpleNamespace(read_note=lambda name: nota)
    enviados: list[str] = []

    async def fake_say(text, count=True):
        enviados.append(text)

    monkeypatch.setattr(orch, "_say_proactive", fake_say)

    cedo = datetime.now().astimezone().replace(hour=7, minute=0)
    await orch._maybe_dream_report(cedo)
    assert enviados == []

    manha = datetime.now().astimezone().replace(hour=8, minute=30)
    await orch._maybe_dream_report(manha)
    assert len(enviados) == 1
    assert "Sonhei com" in enviados[0]

    # Segunda passada no mesmo dia: nao repete.
    await orch._maybe_dream_report(manha.replace(hour=10))
    assert len(enviados) == 1


@pytest.mark.asyncio
async def test_aviso_do_sonho_espera_as_quiet_hours(orch, monkeypatch):
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_notify = True
    orch.cfg.memory.dream_notify_at = "02:00"
    orch.cfg.proactive.quiet_hours = "23:00-08:00"
    ontem = datetime.now().astimezone().date() - timedelta(days=1)
    orch.store.set_meta(f"memory_dreamed:{ontem.isoformat()}", "Sonhos/Sonho x.md")
    enviados: list[str] = []
    monkeypatch.setattr(
        orch, "_say_proactive", lambda text, count=True: enviados.append(text)
    )

    madrugada = datetime.now().astimezone().replace(hour=3, minute=0)
    await orch._maybe_dream_report(madrugada)
    assert enviados == []


@pytest.mark.asyncio
async def test_aviso_sem_sonho_nao_fala(orch, monkeypatch):
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_notify = True
    enviados: list[str] = []
    monkeypatch.setattr(
        orch, "_say_proactive", lambda text, count=True: enviados.append(text)
    )

    await orch._maybe_dream_report(datetime.now().astimezone().replace(hour=9, minute=0))
    assert enviados == []


@pytest.mark.asyncio
async def test_catch_up_sonha_so_de_manha(orch, monkeypatch):
    orch.cfg.memory.dream_enabled = True
    chamadas: list = []

    async def fake_consolidar(day=None, *, force=False, timeout_seconds=None):
        return None

    async def fake_sonhar(day=None, *, force=False, timeout_seconds=None):
        chamadas.append(day)
        return None

    monkeypatch.setattr(orch, "consolidate_day", fake_consolidar)
    monkeypatch.setattr(orch, "dream_day", fake_sonhar)

    agora = datetime.now().astimezone()
    if agora.hour < 12:
        # De manha: o sonho da noite passada e recuperado.
        await orch._dream_catch_up()
        ontem = agora.date() - timedelta(days=1)
        assert chamadas == [ontem]

    # Depois do meio-dia nao se escreve madrugada no meio da tarde.
    tarde = agora.replace(hour=15, minute=0)
    await orch._dream_catch_up()
    if tarde.date() == agora.date() and agora.hour >= 12:
        assert chamadas == []


@pytest.mark.asyncio
async def test_sonhar_na_mao_nao_repete_o_aviso_no_chat(orch, monkeypatch):
    """/sonhar ja mostra o resumo; o laco de memoria nao pode mandar de novo."""
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_notify = True
    orch.cfg.memory.dream_notify_at = "08:00"

    async def fake_consolidar(day=None, *, force=False, timeout_seconds=None):
        return None

    async def fake_sonhar(day=None, *, force=False, timeout_seconds=None):
        return "Sonhos/Sonho x.md"

    enviados: list[str] = []
    monkeypatch.setattr(orch, "consolidate_day", fake_consolidar)
    monkeypatch.setattr(orch, "dream_day", fake_sonhar)
    monkeypatch.setattr(
        orch, "_say_proactive", lambda text, count=True: enviados.append(text)
    )
    nota = types.SimpleNamespace(
        rel_path="Sonhos/Sonho x.md",
        title="x",
        body="Um sonho.\n\n## Pergunta para amanhã\nO que fica?",
        tags=["sonho"],
        links=[],
    )
    orch.brain = types.SimpleNamespace(read_note=lambda name: nota)

    await orch._handle_command("/sonhar 2026-11-20")
    assert orch.store.get_meta("memory_dream_reported:2026-11-20") is not None

    await orch._maybe_dream_report(datetime.now().astimezone().replace(hour=9, minute=0))
    assert enviados == []


@pytest.mark.asyncio
async def test_start_memory_liga_o_worker(orch):
    chamadas: list[bool] = []
    orch.cfg.memory.enabled = True
    orch._memory_task = None
    original = orch._start_memory_worker
    orch._start_memory_worker = lambda: chamadas.append(True)  # type: ignore[method-assign]
    try:
        orch.start_memory()
    finally:
        orch._start_memory_worker = original  # type: ignore[method-assign]
    assert chamadas == [True]


@pytest.mark.asyncio
async def test_aviso_do_sonho_nao_repete_a_cada_minuto_quando_falha(orch, monkeypatch):
    """Telegram fora do ar: espera retry_minutes em vez de tentar 60s a 60s."""
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_notify = True
    orch.cfg.memory.dream_notify_at = "08:00"
    orch.cfg.memory.retry_minutes = 10.0
    ontem = datetime.now().astimezone().date() - timedelta(days=1)
    orch.store.set_meta(f"memory_dreamed:{ontem.isoformat()}", "Sonhos/Sonho x.md")
    nota = types.SimpleNamespace(
        rel_path="Sonhos/Sonho x.md",
        title="x",
        body="Um sonho.\n\n## Pergunta para amanhã\nO que fica?",
        tags=["sonho"],
        links=[],
    )
    orch.brain = types.SimpleNamespace(read_note=lambda name: nota)
    tentativas: list[str] = []

    async def fake_say(text, count=True):
        tentativas.append(text)
        raise RuntimeError("telegram fora do ar")

    monkeypatch.setattr(orch, "_say_proactive", fake_say)
    manha = datetime.now().astimezone().replace(hour=9, minute=0)

    await orch._maybe_dream_report(manha)
    await orch._maybe_dream_report(manha)
    await orch._maybe_dream_report(manha)

    assert len(tentativas) == 1
    assert orch.store.get_meta(f"memory_dream_reported:{ontem.isoformat()}") is None


@pytest.mark.asyncio
async def test_aviso_do_sonho_respeita_proativo_off(orch, monkeypatch):
    """/proativo off e silencio total: o aviso nao fura a escolha dela."""
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_notify = True
    orch.cfg.memory.dream_notify_at = "08:00"
    orch.cfg.proactive.enabled = False
    ontem = datetime.now().astimezone().date() - timedelta(days=1)
    orch.store.set_meta(f"memory_dreamed:{ontem.isoformat()}", "Sonhos/Sonho x.md")
    enviados: list[str] = []
    monkeypatch.setattr(
        orch, "_say_proactive", lambda text, count=True: enviados.append(text)
    )

    await orch._maybe_dream_report(datetime.now().astimezone().replace(hour=9, minute=0))

    assert enviados == []
    assert orch.store.get_meta(f"memory_dream_reported:{ontem.isoformat()}") is None


@pytest.mark.asyncio
async def test_descartar_sonho_move_a_nota_e_nao_avisa_mais(orch, monkeypatch):
    dia = datetime.now().astimezone().date()
    nota = types.SimpleNamespace(
        rel_path=f"Sonhos/Sonho {dia.isoformat()}.md",
        title=f"Sonho {dia.isoformat()}",
        body="Um sonho.\n\n## Pergunta para amanhã\nO que fica?",
        tags=["sonho"],
        links=[],
    )
    orch.brain = types.SimpleNamespace(read_note=lambda name: nota)
    orch.store.set_meta(f"memory_dreamed:{dia.isoformat()}", nota.rel_path)
    orch.store.set_meta(f"memory_dream_reported:{dia.isoformat()}", "2026-11-16T09:00:00")

    def fake_delete(name, confirm=False):
        return f"/lixeira/{name}"

    orch.brain.delete_note = fake_delete  # type: ignore[attr-defined]

    await orch.discard_dream(dia)

    assert orch.store.get_meta(f"memory_dreamed:{dia.isoformat()}") is None
    assert orch.store.get_meta(f"memory_dream_reported:{dia.isoformat()}") is None
    assert orch.store.get_meta(f"memory_dream_forgotten:{dia.isoformat()}") is not None


@pytest.mark.asyncio
async def test_comando_sonhar_descartar(orch, monkeypatch):
    dia = datetime.now().astimezone().date()
    chamadas: list = []

    async def fake_discard(day):
        chamadas.append(day)
        return "/lixeira/Sonhos/Sonho x.md"

    monkeypatch.setattr(orch, "discard_dream", fake_discard)

    await orch._handle_command("/sonhar descartar")

    assert chamadas == [dia]
    assert orch.ui.called("success")


@pytest.mark.asyncio
async def test_dream_report_avisa_quando_a_nota_fica_sem_frase(orch, monkeypatch, caplog):
    """Falha silenciosa era o pior caso: agora fica no log."""
    orch.cfg.memory.dream_enabled = True
    orch.cfg.memory.dream_notify = True
    orch.cfg.memory.dream_notify_at = "08:00"
    ontem = datetime.now().astimezone().date() - timedelta(days=1)
    orch.store.set_meta(f"memory_dreamed:{ontem.isoformat()}", "Sonhos/Sonho x.md")
    orch.brain = types.SimpleNamespace(
        read_note=lambda name: types.SimpleNamespace(
            rel_path="Sonhos/Sonho x.md", title="x", body="", tags=[], links=[]
        )
    )
    enviados: list[str] = []
    monkeypatch.setattr(
        orch, "_say_proactive", lambda text, count=True: enviados.append(text)
    )

    with caplog.at_level("WARNING"):
        await orch._maybe_dream_report(datetime.now().astimezone().replace(hour=9, minute=0))

    assert enviados == []
    assert any("sem frase nem pergunta" in r.message for r in caplog.records)


class FakeClientNota(FakeClient):
    """Modelo que devolve o comentario pessoal do briefing."""

    def __init__(self, texto: str = "Hoje dá pra ir com calma.") -> None:
        super().__init__()
        self.texto = texto
        self.prompts: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    async def create_session(self, **kwargs):
        return types.SimpleNamespace(id="ses-nota")

    async def prompt_sync(self, session_id, prompt, system=None):
        self.prompts.append((prompt, system or ""))
        return types.SimpleNamespace(text=self.texto)

    async def delete_session(self, session_id):
        self.deleted.append(session_id)


@pytest.mark.asyncio
async def test_briefing_personalizado_usa_comentario_do_modelo(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.cfg.briefing.personalize = True
    orch.cfg.briefing.personalize_slots = ["morning"]
    orch.cfg.interface.humor = "alto"
    client = FakeClientNota("Hoje dá pra ir com calma, sem drama.")
    orch.client = client
    dia = datetime.now().astimezone().date()

    assert await orch._send_briefing("morning", dia) is True
    mensagem = orch.telegram.sent[0]
    assert mensagem.rstrip().endswith("sem drama.")
    assert "Bom dia" in mensagem
    assert "Responder a Stefanini" in mensagem

    prompt, system = client.prompts[0]
    # O comentario nasce do contexto real dela, nao de um aviso generico.
    assert "Responder a Stefanini" in prompt
    assert "Metas ativas" in prompt
    assert "humor sempre ligado" in system
    assert client.deleted == ["ses-nota"]


@pytest.mark.asyncio
async def test_briefing_personalizado_so_nos_slots_escolhidos(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.cfg.briefing.personalize = True
    orch.cfg.briefing.personalize_slots = ["noon"]
    client = FakeClientNota()
    orch.client = client
    dia = datetime.now().astimezone().date()

    assert await orch._send_briefing("morning", dia) is True
    assert client.prompts == []
    assert not orch.telegram.sent[0].rstrip().endswith("com calma.")


@pytest.mark.asyncio
async def test_briefing_personalizado_cai_na_lista_se_o_modelo_falhar(orch):
    class ClienteQuebrado(FakeClientNota):
        async def prompt_sync(self, session_id, prompt, system=None):
            raise RuntimeError("modelo fora do ar")

    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.cfg.briefing.personalize = True
    orch.client = ClienteQuebrado()
    dia = datetime.now().astimezone().date()

    assert await orch._send_briefing("morning", dia) is True
    assert orch.telegram.sent[0].startswith("Bom dia")
    assert "Responder a Stefanini" in orch.telegram.sent[0]


@pytest.mark.asyncio
async def test_atalho_troca_sem_consultar_a_rede(orch, monkeypatch):
    import gaia.agent.catalog as catalog_module

    def explode(*args, **kwargs):  # pragma: no cover - so falha se for chamado
        raise AssertionError("atalho não pode listar provedores")

    monkeypatch.setattr(catalog_module, "collect_models", explode)
    orch.cfg.config_file = ""

    responses = await orch.model_messages("cc")

    assert orch.cfg.agent.provider == "commandcode"
    assert orch.cfg.agent.model == "deepseek/deepseek-v4.1-flash"
    assert any("modelo da próxima mensagem" in text for _k, text in responses)


@pytest.mark.asyncio
async def test_atalho_volta_para_o_ultimo_modelo_de_cada_provedor(orch, monkeypatch):
    import gaia.agent.catalog as catalog_module

    def explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("atalho não pode listar provedores")

    monkeypatch.setattr(catalog_module, "collect_models", explode)
    orch.cfg.config_file = ""

    await orch.model_messages("commandcode/z-ai/glm-5.3-flash")
    await orch.model_messages("ds")
    responses = await orch.model_messages("cc")

    assert orch.cfg.agent.provider == "commandcode"
    assert orch.cfg.agent.model == "z-ai/glm-5.3-flash"
    assert any("z-ai/glm-5.3-flash" in text for _k, text in responses)


@pytest.mark.asyncio
async def test_troca_de_provedor_ajusta_o_modelo_rapido(orch, monkeypatch, tmp_path):
    import gaia.agent.catalog as catalog_module

    def explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("slug explícito não deve listar provedores")

    monkeypatch.setattr(catalog_module, "collect_models", explode)
    alvo = tmp_path / "config.toml"
    alvo.write_text(
        '[agent]\nprovider = "commandcode"\nmodel = "m"\n'
        'fast_model = "deepseek/deepseek-v4.1-flash-fast"\n',
        encoding="utf-8",
    )
    orch.cfg.config_file = str(alvo)

    await orch.model_messages("deepseek/deepseek-flash")

    # Na direta o rapido e o proprio V4.1 Flash (id "deepseek-flash"); o id
    # da Command Code nunca vai para o endpoint da DeepSeek.
    assert orch.cfg.agent.fast_model == "deepseek-flash"
    assert 'fast_model = "deepseek-flash"' in alvo.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_atalho_sem_destino_avisa_em_vez_de_virar_modelo(orch, monkeypatch):
    """`zen` sem chave/uso anterior nao pode virar o modelo literal 'zen'."""
    import gaia.agent.catalog as catalog_module

    def explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("atalho conhecido nao deve listar provedores")

    monkeypatch.setattr(catalog_module, "collect_models", explode)
    orch.cfg.config_file = ""

    responses = await orch.model_messages("zen")

    assert responses[0][0] == "warning"
    assert "sem modelo definido" in responses[0][1]
    assert orch.cfg.agent.model != "zen"


async def test_set_proactive_enabled_liga_e_desliga_o_laco(orch):
    """O checkbox da tela de automacoes precisa parar/religar o laco agora."""
    orch.cfg.proactive.enabled = False
    assert orch._proactive_task is None

    assert orch.set_proactive_enabled(True) is True
    assert orch.cfg.proactive.enabled is True
    tarefa = orch._proactive_task
    assert tarefa is not None and not tarefa.done()

    assert orch.set_proactive_enabled(False) is False
    assert orch.cfg.proactive.enabled is False
    assert orch._proactive_task is None
    # O cancelamento e cooperativo: o laco so termina no proximo tick do loop.
    await asyncio.sleep(0)
    assert tarefa.cancelled() or tarefa.done()

    # Religar depois de desligar cria uma tarefa nova (nao reusa a cancelada).
    assert orch.set_proactive_enabled(True) is True
    assert orch._proactive_task is not None and orch._proactive_task is not tarefa
    await orch.aclose()
