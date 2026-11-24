from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from textual import events
from textual.widgets import Input, OptionList

from gaia.agent.catalog import ModelOption
from gaia.controller.modes import PERSONAS
from gaia.interface import textual_app
from gaia.interface.textual_app import (
    _HELP,
    CollapsibleCard,
    GaiaTextualApp,
    TextualSink,
)
from gaia.interface.textual_screens import (
    CommandPalette,
    ModelPicker,
    ModePicker,
    SessionPicker,
)


class _FakeListener:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.paused = False
        self.stopped = False

    def start(self) -> None:
        pass

    def next_utterance(self, timeout=None):
        time.sleep(0.01)
        return None

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def stop(self) -> None:
        self.stopped = True


class _FakeTTS:
    enabled = True

    def stop(self) -> None:
        pass


class _FakeClient:
    async def abort(self, session_id: str) -> None:
        pass


class _FakeRecorder:
    def record_until_silence(self, on_state=None, wait_timeout=None):
        return None


class _FakeTranscriber:
    def transcribe(self, audio, vad_filter=None) -> str:
        return ""


class _FakeFocus:
    current = None


class _FakeGoals:
    def all(self) -> list:
        return []


class _FakeAgenda:
    async def lunation_note(self, date: str) -> str:
        return ""


class _FakeVoice:
    """Voz mínima para exercitar atalhos sem microfone/rede."""

    def __init__(self) -> None:
        self.cfg = SimpleNamespace(
            audio=SimpleNamespace(
                sample_rate=16000,
                channels=1,
                input_device="",
                silence_ms=5000,
                max_seconds=0.0,
                speech_threshold_db=-40.0,
                min_speech_ms=250.0,
                preroll_ms=300.0,
            ),
            interface=SimpleNamespace(chinese_echo=False),
            agent=SimpleNamespace(model_slug=lambda: "x/y"),
        )
        self.tts = _FakeTTS()
        self.client = _FakeClient()
        self.recorder = _FakeRecorder()
        self.transcriber = _FakeTranscriber()
        self.focus = _FakeFocus()
        self.goals = _FakeGoals()
        self.agenda = _FakeAgenda()
        self.session_id = "ses_fake"
        self.mode_key = "assistente"
        self.mode_name = "Gaia"
        self.selected_modes: list[str] = []
        self.model_calls: list[str] = []
        self.spoken: list[str] = []
        self.model_options: list = [
            ModelOption(provider="x", model="y", current=True),
            ModelOption(provider="p", model="q"),
        ]

    def modes(self) -> list:
        return list(PERSONAS.values())

    def history(self) -> list:
        return []

    def sessions(self) -> list:
        return []

    async def select_mode(self, key: str) -> None:
        self.mode_key = key
        self.selected_modes.append(key)

    async def new_session(self) -> None:
        pass

    async def select_conversation(self, conversation_id: int) -> None:
        pass

    async def speak_text(self, text: str) -> None:
        self.spoken.append(text)

    async def translate_zh(self, text: str) -> str:
        return ""

    def mark_session_dirty(self) -> None:
        pass

    async def show_model_options(self):
        return [("info", "lista")], list(self.model_options)

    async def model_messages(self, argument: str):
        self.model_calls.append(argument)
        return [("success", f"modelo da próxima mensagem: {argument}")]


async def _answer(text: str, sink) -> str:
    sink.token(f"eco: {text}")
    return f"eco: {text}"


def test_app_pode_iniciar_no_modo_selecao():
    async def _answer(text, sink):
        return "ok"

    normal = GaiaTextualApp(_answer)
    selecao = GaiaTextualApp(_answer, start_selecting=True)

    assert normal._selection_mode is False
    assert selecao._selection_mode is True


@pytest.mark.asyncio
async def test_app_textual_responde_sem_erro():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("i")
        field = app.query_one("#input", textual_app.GaiaInput)
        field.text = "oi"
        await pilot.press("enter")
        await pilot.pause()
    assert True


@pytest.mark.asyncio
async def test_submit_text_finishes_with_answer():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        captured: dict[str, str] = {}
        app.finish_stream = lambda text: captured.setdefault("text", text)

        await app.submit_text("oi")
        await pilot.pause()

    assert captured["text"] == "eco: oi"
    assert app._turn_running is False


@pytest.mark.asyncio
async def test_submit_text_reports_answer_errors():
    async def broken(text: str, sink) -> str:
        raise RuntimeError("boom")

    app = GaiaTextualApp(broken)
    async with app.run_test() as pilot:
        await pilot.pause()
        captured: dict[str, str] = {}
        app.finish_stream = lambda text: captured.setdefault("text", text)

        await app.submit_text("oi")
        await pilot.pause()

    assert captured["text"] == "Erro: boom"
    assert app._turn_running is False


@pytest.mark.asyncio
async def test_submit_text_ignores_second_turn_while_running():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        calls: list[str] = []
        app.finish_stream = lambda text: calls.append(text)
        app._turn_running = True

        await app.submit_text("ignorado")
        await pilot.pause()

        assert calls == []
        assert app._turn_running is True
        assert app._status_base.startswith("aguarde")


@pytest.mark.asyncio
async def test_help_command_writes_help_note():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        written: list[str] = []
        app._write_note = lambda text: written.append(text)

        event = SimpleNamespace(value="/ajuda", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert written == [_HELP]


@pytest.mark.asyncio
async def test_quit_command_exits_app():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[object] = []
        app.exit = lambda code=None: exits.append(code)

        event = SimpleNamespace(value="/sair", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert exits == [None]


@pytest.mark.asyncio
async def test_plain_text_is_sent_to_answer():
    app = GaiaTextualApp(_answer)
    received: list[str] = []

    async def fake_submit(text: str) -> None:
        received.append(text)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.submit_text = fake_submit

        event = SimpleNamespace(value="olá gaia", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)
        await asyncio.sleep(0)
        await pilot.pause()

    assert received == ["olá gaia"]


@pytest.mark.asyncio
async def test_paste_multilinha_preserva_o_texto_como_veio():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        field = app.query_one("#input", textual_app.GaiaInput)
        field.focus()

        app.post_message(events.Paste("primeira linha\nsegunda linha\n\nterceira linha"))
        await pilot.pause()

        assert field.text == "primeira linha\nsegunda linha\n\nterceira linha"


@pytest.mark.asyncio
async def test_enter_envia_o_texto_colado_e_limpa_o_campo():
    app = GaiaTextualApp(_answer)
    received: list[str] = []

    async def fake_submit(text: str) -> None:
        received.append(text)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.submit_text = fake_submit
        field = app.query_one("#input", textual_app.GaiaInput)
        field.focus()

        app.post_message(events.Paste("primeira linha\nsegunda linha"))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert received == ["primeira linha\nsegunda linha"]
        assert field.text == ""


@pytest.mark.asyncio
async def test_voice_toggle_without_voice_reports_unavailable():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.action_toggle_voice()

    assert app._status_base.startswith("voz indisponível")


def test_sink_buffers_and_streams():
    updates: list[str] = []
    statuses: list[str] = []
    fake = SimpleNamespace(
        update_stream=updates.append,
        _set_status=statuses.append,
        write_diff=lambda *a: None,
    )
    sink = TextualSink(fake)

    sink.token("olá ")
    sink.token("mundo")
    sink.tool("read", "arquivo")

    assert sink.buffer == "olá mundo"
    assert updates == ["olá "]
    assert statuses and statuses[-1].startswith("▸ read")


def test_set_status_is_safe_before_mount():
    app = GaiaTextualApp(_answer)
    app._set_status("antes do mount")
    assert app._status_base == "antes do mount"


@pytest.mark.asyncio
async def test_set_status_is_safe_after_unmount():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._set_status("durante")
        assert app._status_base == "durante"
    app._set_status("depois")
    assert app._status_base == "depois"


@pytest.mark.asyncio
async def test_startup_sem_modal_de_modo():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1  # nenhum modal na abertura
        assert app._exception is None


@pytest.mark.asyncio
async def test_tab_abre_modal_de_modo_sem_trocar():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        assert voice.mode_key == "assistente"
        assert voice.selected_modes == []
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert voice.mode_key == "assistente"


@pytest.mark.asyncio
async def test_f6_abre_seletor_de_modelos():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker)
        assert voice.model_calls == []

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert voice.model_calls == []


@pytest.mark.asyncio
async def test_seletor_de_modelos_aplica_a_escolha():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker)

        await pilot.press("down")
        await pilot.press("enter")
        for _ in range(20):
            if voice.model_calls:
                break
            await pilot.pause()

        assert voice.model_calls == ["p/q"]
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_modelo_com_argumento_troca_direto():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        app._handle_modelo_command("/modelo p/q")
        for _ in range(20):
            if voice.model_calls:
                break
            await pilot.pause()

        assert voice.model_calls == ["p/q"]
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_modelo_sem_argumento_abre_seletor():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        app._handle_modelo_command("/modelo")
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker)
        assert voice.model_calls == []


@pytest.mark.asyncio
async def test_modebar_mostra_modo_e_sessoes_clicavel():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        texto = app._modebar.content
        assert "Gaia" in str(texto)
        assert "Tab modo" not in str(texto)
        assert "Sessões" not in str(texto)
        assert "Sessões" in str(app.query_one("#session-button").content)

        await pilot.click("#session-button")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)


@pytest.mark.asyncio
async def test_shortcuts_work_with_voice_and_modals(monkeypatch):
    monkeypatch.setattr(textual_app, "ContinuousListener", _FakeListener)
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()

        keys = [
            "v",
            "f",
            "c",
            "m",
            "t",
            "escape",
            "f1",
            "escape",
            "f2",
            "escape",
            "f3",
            "escape",
            "ctrl+t",
            "ctrl+l",
            "i",
            "escape",
            "/",
            "escape",
        ]
        for key in keys:
            await pilot.press(key)
            await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app._insert_mode is False
        assert app._exception is None


@pytest.mark.asyncio
async def test_palette_escape_closes_without_submitting():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        submitted: list[str] = []

        async def fake_submit(text: str) -> None:
            submitted.append(text)

        app.submit_text = fake_submit
        await pilot.press("f1")
        await pilot.pause()
        assert len(app.screen_stack) == 2

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert submitted == []


@pytest.mark.asyncio
async def test_paleta_busca_e_enter_abre_o_comando():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)

        field = app.screen.query_one("#palette-input", Input)
        field.value = "modo"
        await pilot.pause()
        options = app.screen.query_one("#palette-list", OptionList)
        assert options.option_count == 1

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)


@pytest.mark.asyncio
async def test_paleta_setas_escolhem_comando():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        options = app.screen.query_one("#palette-list", OptionList)
        assert options.highlighted == 0

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 1

        await pilot.press("up")
        await pilot.pause()
        assert options.highlighted == 0


@pytest.mark.asyncio
async def test_f_liga_modo_voz_e_enter_volta_ao_texto(monkeypatch):
    monkeypatch.setattr(textual_app, "ContinuousListener", _FakeListener)
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)

    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()

        assert app._continuous is True
        assert app._voice_replies is True
        assert app._listener is not None
        assert voice.tts.enabled is True
        assert "voz" in app._status_base

        await pilot.press("enter")
        await pilot.pause()

        assert app._continuous is False
        assert app._voice_replies is False
        assert app._listener is None
        assert voice.tts.enabled is False
        assert app._insert_mode is True
        assert "TEXTO" in app._status_base

    assert app._exception is None


@pytest.mark.asyncio
async def test_v_liga_modo_voz_e_t_volta_ao_texto(monkeypatch):
    monkeypatch.setattr(textual_app, "ContinuousListener", _FakeListener)
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)

    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert app._continuous is True
        assert app._voice_replies is True

        await pilot.press("t")
        await pilot.pause()
        assert app._continuous is False
        assert app._voice_replies is False
        assert voice.tts.enabled is False

    assert app._exception is None


@pytest.mark.asyncio
async def test_resposta_e_falada_quando_modo_voz_esta_ligado():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._voice_replies = True
        await app.submit_text("oi")
        for _ in range(20):
            if voice.spoken:
                break
            await pilot.pause()

        assert voice.spoken == ["eco: oi"]
        assert app._turn_running is False


@pytest.mark.asyncio
async def test_eco_de_idiomas_desenha_o_rotulo_escolhido():
    """Com o eco ligado o rotulo e o do idioma escolhido (nao fixo "中文")."""
    voice = _FakeVoice()
    voice.cfg.interface.chinese_echo = True
    traducoes: list[tuple[str, str]] = []

    async def translate_echo(text: str, lang: str | None = None) -> str:
        traducoes.append((text, lang or ""))
        return f"({lang}) {text}"

    voice.translate_echo = translate_echo  # type: ignore[attr-defined]
    app = GaiaTextualApp(_answer, voice=voice)

    async with app.run_test() as pilot:
        await pilot.pause()
        for idioma, rotulo in (("en", "English"), ("fr", "Français")):
            voice.cfg.interface.echo_language = idioma
            await app._echo_zh("bom dia")
            await pilot.pause()
            ultimo = app._chat_ref.children[-1].render()
            assert rotulo in ultimo.plain

    assert traducoes == [("bom dia", "en"), ("bom dia", "fr")]


@pytest.mark.asyncio
async def test_chines_liga_o_idioma_escolhido_na_tui():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._toggle_chinese("/chines fr")
        assert voice.cfg.interface.chinese_echo is True
        assert voice.cfg.interface.echo_language == "fr"
        app._toggle_chinese("/chines off")
        assert voice.cfg.interface.chinese_echo is False
        # O idioma escolhido sobrevive ao desligar.
        assert voice.cfg.interface.echo_language == "fr"


@pytest.mark.asyncio
async def test_eco_desligado_nao_traduz():
    voice = _FakeVoice()
    voice.cfg.interface.chinese_echo = False
    traducoes: list[str] = []

    async def translate_echo(text: str, lang: str | None = None) -> str:
        traducoes.append(text)
        return text

    voice.translate_echo = translate_echo  # type: ignore[attr-defined]
    app = GaiaTextualApp(_answer, voice=voice)

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.submit_text("oi")
        await pilot.pause()

    assert traducoes == []


@pytest.mark.asyncio
async def test_resposta_nao_e_falada_no_modo_texto():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.submit_text("oi")
        await pilot.pause()

        assert voice.spoken == []
        assert app._turn_running is False


class _ListenerComUmaFala(_FakeListener):
    """Entrega uma fala e depois so silencio (como o microfone real)."""

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._entregues = 0

    def next_utterance(self, timeout=None):
        time.sleep(0.01)
        if self._entregues:
            return None
        self._entregues += 1
        return [0.0] * 1600


@pytest.mark.asyncio
async def test_escuta_continua_transcreve_e_submete(monkeypatch):
    """Caminho mic -> STT -> submit: o que mais falha em maquina nova."""
    monkeypatch.setattr(textual_app, "ContinuousListener", _ListenerComUmaFala)
    voice = _FakeVoice()
    voice.transcriber = SimpleNamespace(
        transcribe=lambda audio, vad_filter=None: "  olá por voz  "
    )
    app = GaiaTextualApp(_answer, voice=voice)
    received: list[str] = []

    async def fake_submit(text: str) -> None:
        received.append(text)

    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        app.submit_text = fake_submit
        await pilot.press("f")
        for _ in range(50):
            if received:
                break
            await pilot.pause()
        app._leave_voice_mode()
        await pilot.pause()

    assert received == ["olá por voz"]
    assert app._exception is None


@pytest.mark.asyncio
async def test_falha_de_transcricao_mostra_o_motivo_real():
    """Antes virava 'não entendi'; agora mostra a causa (modelo, download)."""
    app = GaiaTextualApp(_answer, voice=_FakeVoice())

    def boom(audio, vad_filter=None):
        raise RuntimeError("modelo base indisponivel")

    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        result = await app._transcribe(SimpleNamespace(transcribe=boom), [0.0])

    assert result is None
    assert "modelo base indisponivel" in app._status_base


@pytest.mark.asyncio
async def test_palette_input_does_not_submit_chat():
    app = GaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        received: list[str] = []

        async def fake_submit(text: str) -> None:
            received.append(text)

        app.submit_text = fake_submit
        event = SimpleNamespace(value="oi", input=SimpleNamespace(value="", id="palette-input"))
        await app.on_input_submitted(event)
        await asyncio.sleep(0)
        await pilot.pause()

    assert received == []


@pytest.mark.asyncio
async def test_troca_de_modo_usa_worker_serializado(monkeypatch):
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        captured: dict[str, object] = {}

        def fake_run_worker(work, **kwargs):
            captured.update(kwargs)
            close = getattr(work, "close", None)
            if close is not None:
                close()
            return None

        monkeypatch.setattr(app, "run_worker", fake_run_worker)
        app._switch_mode("investidora")
        await pilot.pause()

    assert captured.get("group") == "modo"
    assert captured.get("exclusive") is True


@pytest.mark.asyncio
async def test_menu_button_abre_paleta_de_atalhos():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#menu-button")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_tab_abre_modal_mesmo_digitando():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        assert voice.mode_key == "assistente"
        assert voice.selected_modes == []


@pytest.mark.asyncio
async def test_click_modebar_abre_modal_de_modo():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#modebar")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        assert voice.mode_key == "assistente"
        assert voice.selected_modes == []


@pytest.mark.asyncio
async def test_clicar_painel_recolhe_e_expande():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        painel = app.query_one(".dashboard-panel", CollapsibleCard)
        assert painel.expanded is False

        await pilot.click(".dashboard-panel")
        await pilot.pause()
        assert painel.expanded is True

        await pilot.click(".dashboard-panel")
        await pilot.pause()
        assert painel.expanded is False


@pytest.mark.asyncio
async def test_painel_abre_expandido_quando_configurado():
    voice = _FakeVoice()
    voice.cfg.interface.dashboard_collapsed = False
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        painel = app.query_one(".dashboard-panel", CollapsibleCard)
        assert painel.expanded is True


@pytest.mark.asyncio
async def test_f5_mostra_e_oculta_lua_e_metas():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        paineis = list(app.query(".dashboard-panel"))
        assert paineis
        assert all(widget.display for widget in paineis)

        await pilot.press("f5")
        await pilot.pause()
        assert not any(widget.display for widget in paineis)
        assert "ocultas" in app._status_base

        await pilot.press("f5")
        await pilot.pause()
        assert all(widget.display for widget in paineis)


@pytest.mark.asyncio
async def test_f4_liga_e_desliga_selecao_de_texto():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        assert app._selection_mode is False
        assert app.ALLOW_SELECT is False

        app.action_toggle_mouse()
        assert app._selection_mode is True
        assert app.ALLOW_SELECT is True
        assert "seleção ligada" in app._status_base

        app.action_toggle_mouse()
        assert app._selection_mode is False
        assert app.ALLOW_SELECT is False
        assert "seleção desligada" in app._status_base
        await pilot.pause()


@pytest.mark.asyncio
async def test_abre_com_selecao_quando_configurado():
    app = GaiaTextualApp(_answer, voice=_FakeVoice(), start_selecting=True)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        assert app.ALLOW_SELECT is True
        assert "seleção ligada" in app._status_base


@pytest.mark.asyncio
async def test_arrastar_seleciona_texto_e_copia():
    app = GaiaTextualApp(_answer, voice=_FakeVoice(), start_selecting=True)
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        copiados: list[str] = []
        app._copy_text = copiados.append  # evita pbcopy no teste
        app._clear_messages()
        app._mount("linha de teste para copiar")
        await pilot.pause()
        widget = list(app._chat().children)[-1]
        region = widget.region
        await pilot.mouse_down(offset=(region.x + 1, region.y))
        await pilot.mouse_up(offset=(region.x + 20, region.y))
        await pilot.pause()
        texto = app.screen.get_selected_text()
        assert "teste" in texto
        assert copiados == [texto]
        assert "copiado" in app._status_base


@pytest.mark.asyncio
async def test_ctrl_c_copia_selecao():
    app = GaiaTextualApp(_answer, voice=_FakeVoice(), start_selecting=True)
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        app._clear_messages()
        app._mount("linha de teste para copiar")
        await pilot.pause()
        widget = list(app._chat().children)[-1]
        region = widget.region
        await pilot.mouse_down(offset=(region.x + 1, region.y))
        await pilot.mouse_up(offset=(region.x + 20, region.y))
        await pilot.pause()
        texto = app.screen.get_selected_text()
        assert texto
        app._clipboard = ""  # type: ignore[misc]
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.clipboard == texto


@pytest.mark.asyncio
async def test_arrastar_no_painel_nao_recolhe_e_clique_recolhe():
    app = GaiaTextualApp(_answer, voice=_FakeVoice(), start_selecting=True)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        painel = app.query_one(".dashboard-panel", CollapsibleCard)
        assert painel.expanded is False

        region = painel.region
        await pilot.mouse_down(offset=(region.x + 2, region.y))
        await pilot.mouse_up(offset=(region.x + 20, region.y))
        await pilot.pause()
        assert app.screen.selections
        assert painel.expanded is False

        # Um clique simples depois do arrasto volta a funcionar.
        await pilot.click(".dashboard-panel")
        await pilot.pause()
        assert painel.expanded is True

        await pilot.click("#menu-button")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_modal_de_modo_aplica_modo_escolhido():
    voice = _FakeVoice()
    app = GaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        # O fake abre em "assistente" (chave antiga): nenhum bot atual casa,
        # entao o destaque comeca no primeiro e "up" da a volta no ultimo.
        # Nao fixe o nome do bot aqui: o registry cresce (fase 1 = 5 bots).
        ultimo = list(PERSONAS)[-1]
        await pilot.press("up")
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert voice.mode_key == ultimo
        assert voice.selected_modes == [ultimo]


@pytest.mark.asyncio
async def test_resposta_usa_markdown_por_padrao():
    from gaia.interface.textual_markdown import GaiaMarkdown

    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app._body("# Título\n\n**forte**"), GaiaMarkdown)


@pytest.mark.asyncio
async def test_markdown_pode_ser_desligado_na_config():
    app = GaiaTextualApp(_answer, voice=_FakeVoice())
    app._voice.cfg.interface.markdown_render = False
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        body = app._body("# Título\n\n**forte**")
        assert not hasattr(body, "elements")
        assert "# Título" in body.plain


# ------------------------------------------------------------------- modelos
@pytest.mark.asyncio
async def test_f6_sem_modelos_avisa_e_nao_abre_modal():
    class SemModelos(_FakeVoice):
        async def show_model_options(self):
            return [("warning", "nenhum modelo")], []

    app = GaiaTextualApp(_answer, voice=SemModelos())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert len(app.screen_stack) == 1
