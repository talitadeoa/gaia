from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from gaia.config import Config, TelegramConfig
from gaia.interface.terminal import TerminalUI
from gaia.telegram.client import TelegramClient, TelegramError, _split_text
from gaia.telegram.service import _COMMANDS_MENU, TelegramService, telegram_permission


def _cfg(**overrides) -> TelegramConfig:
    data = {
        "enabled": True,
        "bot_token": "123:abc",
        "allowed_chat_id": "42",
    }
    data.update(overrides)
    return TelegramConfig(**data)


@pytest.mark.asyncio
async def test_send_message_endpoint_e_payload():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    result = await client.send_message("olá")
    await http.aclose()

    assert result["message_id"] == 7
    request = captured[0]
    assert request.url.path == "/bot123:abc/sendMessage"
    payload = json.loads(request.content)
    assert payload["chat_id"] == "42"
    assert payload["text"] == "olá"


@pytest.mark.asyncio
async def test_send_voice_usa_sendvoice(tmp_path, monkeypatch):
    import gaia.telegram.client as mod

    source = tmp_path / "a.mp3"
    source.write_bytes(b"fake")
    converted = tmp_path / "a.ogg"
    converted.write_bytes(b"OggS")
    monkeypatch.setattr(mod, "_to_ogg_opus", lambda _path: converted)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    result = await client.send_audio(source, as_voice=True)
    await http.aclose()

    assert result["message_id"] == 5
    assert calls[0].endswith("/sendVoice")


@pytest.mark.asyncio
async def test_set_my_commands_envia_payload():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    await client.set_my_commands([("ajuda", "lista dos comandos"), ("status", "estado")])
    await http.aclose()

    assert captured[0].url.path == "/bot123:abc/setMyCommands"
    payload = json.loads(captured[0].content)
    assert payload["commands"] == [
        {"command": "ajuda", "description": "lista dos comandos"},
        {"command": "status", "description": "estado"},
    ]


@pytest.mark.asyncio
async def test_get_updates_e_download():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={"ok": True, "result": [{"update_id": 1, "message": {"text": "oi"}}]},
            )
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voz/1.ogg"}})
        if request.url.path == "/file/bot123:abc/voz/1.ogg":
            return httpx.Response(200, content=b"OggS")
        return httpx.Response(404, json={"ok": False, "description": "not found"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    updates = await client.get_updates()
    path = await client.get_file("file-1")
    data = await client.download_file(path)
    await http.aclose()

    assert updates[0]["update_id"] == 1
    assert path == "voz/1.ogg"
    assert data == b"OggS"


@pytest.mark.asyncio
async def test_erro_do_telegram_vira_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    with pytest.raises(TelegramError) as exc:
        await client.send_message("oi")
    await http.aclose()
    assert exc.value.status == 401


def test_split_text_respeita_limite():
    text = ("palavra " * 1000).strip()
    chunks = _split_text(text, limit=100)
    assert chunks
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert " ".join(chunks).startswith("palavra")


def test_config_redige_token_do_telegram():
    cfg = Config()
    cfg.telegram.bot_token = "segredo"
    assert cfg.public_dict()["telegram"]["bot_token"] == "***"


def test_permissao_do_telegram_segue_o_flag():
    assert telegram_permission(_cfg(auto_approve=True))("agenda_nova_tarefa", []) == "always"
    assert telegram_permission(_cfg())("agenda_nova_tarefa", []) == "reject"
    assert telegram_permission(_cfg())("write", ["a.txt"]) == "reject"
    assert telegram_permission(_cfg())("bash", ["pytest"]) == "reject"


def test_permissao_do_telegram_nega_shell_mesmo_com_auto_approve():
    """O chat remoto não pode virar shell na máquina sem opt-in explícito."""
    decide = telegram_permission(_cfg(auto_approve=True))
    assert decide("bash", ["rm -rf ~"]) == "reject"
    assert decide("write", ["a.txt"]) == "reject"
    assert decide("edit", ["a.py"]) == "reject"
    assert decide("read", ["a.py"]) == "always"
    assert decide("agenda_dia", []) == "always"


def test_shell_access_libera_com_auto_approve():
    cfg = _cfg(auto_approve=True, shell_access=True)
    assert telegram_permission(cfg)("bash", ["pytest"]) == "always"
    assert telegram_permission(_cfg(shell_access=True))("bash", ["pytest"]) == "reject"


def test_permissao_do_telegram_exige_chat_fixado():
    cfg = _cfg(auto_approve=True, allowed_chat_id="")
    assert telegram_permission(cfg)("agenda_dia", []) == "reject"
    assert telegram_permission(cfg)("bash", ["pytest"]) == "reject"


def test_service_restringe_chat_configurado():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    service = TelegramService(cfg, None, None, None, TerminalUI(quiet=True), logging.getLogger("t"))
    assert service._allowed(42) is True
    assert service._allowed(99) is False


def test_service_adota_primeiro_chat_quando_vazio():
    import logging

    cfg = Config()
    service = TelegramService(cfg, None, None, None, TerminalUI(quiet=True), logging.getLogger("t"))
    assert service._allowed(7) is True
    assert service._allowed(7) is True
    assert service._allowed(8) is False


class _FakeOrchestrator:
    last_error = "Location not found: /Users/talita/zora"

    async def answer_once(self, text, speak=False, close=False):
        return ""


class _BootOrchestrator:
    """Orquestrador minimo para provar o que o `serve` liga ao subir."""

    last_error = ""

    def __init__(self) -> None:
        self.ligados: list[str] = []

    def start_briefing(self) -> None:
        self.ligados.append("briefing")

    def start_career(self) -> None:
        self.ligados.append("career")

    def start_proactive(self) -> None:
        self.ligados.append("proactive")

    def start_memory(self) -> None:
        self.ligados.append("memory")

    async def answer_once(self, text, speak=False, close=False):
        return ""


class _GetMeClient:
    def __init__(self) -> None:
        self.menu: list[tuple[str, str]] | None = None

    async def get_me(self):
        return {"username": "teste"}

    async def set_my_commands(self, commands):
        self.menu = list(commands)
        return True

    async def get_updates(self, offset=None):
        # Dorme ate o teste cancelar, sem girar em loop.
        await asyncio.sleep(3600)
        return []


@pytest.mark.asyncio
async def test_serve_liga_briefing_e_rotina_de_vagas_ao_subir():
    """`telegram serve` nao chama bootstrap; os workers sobem no boot."""
    import asyncio
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    orch = _BootOrchestrator()
    tg = _GetMeClient()
    service = TelegramService(
        cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    task = asyncio.create_task(service.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert orch.ligados == ["briefing", "career", "proactive", "memory"]


@pytest.mark.asyncio
async def test_serve_registra_menu_de_comandos_no_boot():
    """O botao ``/`` do chat mostra o menu ao subir o serviço."""
    import asyncio
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _GetMeClient()
    service = TelegramService(
        cfg, _BootOrchestrator(), tg, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    task = asyncio.create_task(service.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert tg.menu is not None
    assert [cmd for cmd, _ in tg.menu] == [cmd for cmd, _ in _COMMANDS_MENU]


class _BoomGetMeClient(_GetMeClient):
    async def get_me(self):
        raise TelegramError("erro do Telegram: Bad Gateway", 502)


@pytest.mark.asyncio
async def test_serve_sobrevive_a_bad_gateway_no_boot():
    """502 no getMe nao pode derrubar o serve (o launchd faria crash-loop)."""
    import asyncio
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    orch = _BootOrchestrator()
    service = TelegramService(
        cfg, orch, _BoomGetMeClient(), None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    task = asyncio.create_task(service.run())
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert orch.ligados == ["briefing", "career", "proactive", "memory"]


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.audios: list[str] = []

    async def send_chat_action(self, *args, **kwargs) -> None:
        pass

    async def send_message(self, text, chat_id=None, **kwargs) -> None:
        self.sent.append(text)

    async def send_audio(self, path, chat_id=None, caption="", as_voice=False) -> None:
        self.audios.append(str(path))


@pytest.mark.asyncio
async def test_erro_do_agente_chega_no_telegram():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    service = TelegramService(
        cfg, _FakeOrchestrator(), tg, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "oi"}})

    assert any("Location not found" in text for text in tg.sent)
    assert "Concluído." not in tg.sent



class _NewSessionOrchestrator:
    last_error = ""

    def __init__(self) -> None:
        self.novas = 0

    async def new_session(self) -> None:
        self.novas += 1

    async def answer_once(self, text, speak=False, close=False):
        return "resposta do agente"


@pytest.mark.asyncio
async def test_novo_cria_sessao_e_nao_chama_o_agente():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _NewSessionOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/novo"}})

    assert orch.novas == 1
    assert all("resposta do agente" not in text for text in tg.sent)
    assert any("sessão nova" in text for text in tg.sent)


class _ModelOrchestrator:
    last_error = ""

    def __init__(self) -> None:
        self.argumentos: list[str] = []

    async def model_messages(self, argument: str):
        self.argumentos.append(argument)
        if not argument:
            return [
                ("info", " 1. ● deepseek/deepseek-flash"),
                ("info", " 2. ○ deepseek/deepseek-chat"),
                ("info", "use /modelo <número> ou /modelo provider/model"),
            ]
        return [("success", f"modelo da próxima mensagem: {argument}"), ("info", "salvo")]

    async def answer_once(self, text, speak=False, close=False):
        return "resposta do agente"


@pytest.mark.asyncio
async def test_modelo_no_telegram_lista_modelos():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _ModelOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/modelo"}})

    assert orch.argumentos == [""]
    texto = tg.sent[-1]
    assert "deepseek/deepseek-flash" in texto
    assert "deepseek/deepseek-chat" in texto
    assert all("resposta do agente" not in item for item in tg.sent)


@pytest.mark.asyncio
async def test_modelo_no_telegram_troca_pelo_slug():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _ModelOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update(
        {"message": {"chat": {"id": 42}, "text": "/modelo deepseek/deepseek-chat"}}
    )

    assert orch.argumentos == ["deepseek/deepseek-chat"]
    assert "modelo da próxima mensagem" in tg.sent[-1]


@pytest.mark.asyncio
async def test_modelos_sem_argumento_no_telegram_lista():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _ModelOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/modelos"}})

    assert orch.argumentos == [""]
    assert "deepseek/deepseek-flash" in tg.sent[-1]


class _VoiceOrchestrator:
    """Orquestrador com TTS que devolve um arquivo pronto."""

    last_error = ""

    class _TTS:
        class _Backend:
            def cleanup(self, path) -> None:
                pass

        async def prepare(self, text):
            return (self._Backend(), "/tmp/reply.mp3")

    def __init__(self) -> None:
        self.tts = self._TTS()
        self.received: list[str] = []

    async def answer_once(self, text, speak=False, close=False):
        self.received.append(text)
        return f"resposta a {text}"


@pytest.mark.asyncio
async def test_audio_entrando_volta_como_audio(monkeypatch):
    """Falar por voz deve receber voz de volta, sem ligar voice_replies."""
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    cfg.telegram.voice_replies = False
    tg = _FakeTelegramClient()
    orch = _VoiceOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    async def fake_transcribe(message):
        return "olha minha agenda"

    monkeypatch.setattr(service, "_transcribe_message", fake_transcribe)

    await service._handle_update(
        {"message": {"chat": {"id": 42}, "voice": {"file_id": "x", "mime_type": "audio/ogg"}}}
    )

    assert orch.received == ["olha minha agenda"]
    assert tg.audios == ["/tmp/reply.mp3"]


@pytest.mark.asyncio
async def test_texto_entrando_nao_gera_audio_por_padrao():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    cfg.telegram.voice_replies = False
    tg = _FakeTelegramClient()
    service = TelegramService(
        cfg, _VoiceOrchestrator(), tg, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "oi"}})

    assert tg.audios == []


class _MetaStore:
    """Store minimo: so o meta que o offset usa."""

    def __init__(self) -> None:
        self.meta: dict[str, str] = {}

    def get_meta(self, key, default=None):
        return self.meta.get(key, default)

    def set_meta(self, key, value):
        self.meta[key] = str(value)


class _OffsetOrchestrator:
    last_error = ""

    def __init__(self) -> None:
        self.store = _MetaStore()
        self.recebidas: list[str] = []

    def start_briefing(self) -> None:
        pass

    def start_career(self) -> None:
        pass

    def start_proactive(self) -> None:
        pass

    def start_memory(self) -> None:
        pass

    async def answer_once(self, text, speak=False, close=False):
        self.recebidas.append(text)
        return "ok"


class _OffsetClient:
    def __init__(self) -> None:
        self.offsets: list[int | None] = []
        self.enviadas: list[str] = []

    async def get_me(self):
        return {"username": "teste"}

    async def set_my_commands(self, commands):
        return True

    async def send_chat_action(self, *args, **kwargs) -> None:
        pass

    async def send_message(self, text, chat_id=None, **kwargs) -> None:
        self.enviadas.append(text)

    async def get_updates(self, offset=None):
        self.offsets.append(offset)
        if len(self.offsets) == 1:
            return [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "oi"}}]
        # Dorme ate o teste cancelar, sem girar em loop.
        await asyncio.sleep(3600)
        return []


@pytest.mark.asyncio
async def test_serve_retoma_offset_salvo_e_persiste_o_novo():
    """Restart nao pode reentregar update ja respondido: offset fica no meta."""
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    orch = _OffsetOrchestrator()
    orch.store.meta["telegram:offset"] = "7"
    client = _OffsetClient()
    service = TelegramService(
        cfg, orch, client, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    task = asyncio.create_task(service.run())
    for _ in range(100):
        if len(client.offsets) > 1:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.offsets[0] == 7
    assert orch.recebidas == ["oi"]
    assert client.offsets[1] == 8
    assert orch.store.meta["telegram:offset"] == "8"


def test_offset_salvo_invalido_nao_quebra_o_boot():
    import logging

    cfg = Config()
    orch = _OffsetOrchestrator()
    orch.store.meta["telegram:offset"] = "lixo"
    service = TelegramService(
        cfg, orch, None, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    assert service._load_offset() is None


def test_servico_sem_store_nao_quebra_o_offset():
    import logging

    cfg = Config()
    service = TelegramService(
        cfg, None, None, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    assert service._load_offset() is None
    service._save_offset(9)


def test_offset_persiste_no_sqlite_de_verdade(tmp_path):
    """Round-trip com o MemoryStore real: o meta precisa sobreviver ao processo."""
    import logging

    from gaia.memory.store import MemoryStore

    cfg = Config()
    orch = _OffsetOrchestrator()
    db = tmp_path / "gaia.db"
    orch.store = MemoryStore(db)
    service = TelegramService(
        cfg, orch, None, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    service._save_offset(4312)
    orch.store.close()

    reaberto = MemoryStore(db)
    try:
        service2 = TelegramService(
            cfg,
            type("O", (), {"store": reaberto})(),
            None,
            None,
            TerminalUI(quiet=True),
            logging.getLogger("t"),
        )
        assert service2._load_offset() == 4312
    finally:
        reaberto.close()


# --------------------------------------------------------------- raciocínio
class _FakeThinkingClient:
    """Cliente minimo que registra envio e edicao do raciocinio."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edits: list[tuple[int, str]] = []

    async def send_message(self, text, chat_id=None, **kwargs):
        self.sent.append(text)
        return {"message_id": len(self.sent)}

    async def edit_message_text(self, message_id, text, chat_id=None, **kwargs):
        self.edits.append((message_id, text))
        return {"message_id": message_id}

    async def send_chat_action(self, *args, **kwargs) -> None:
        pass


@pytest.mark.asyncio
async def test_thinking_sink_envia_uma_vez_e_depois_edita():
    import logging

    from gaia.telegram.thinking import ThinkingSink

    client = _FakeThinkingClient()
    sink = ThinkingSink(client, logging.getLogger("t"), 42, min_interval=0.0)
    sink.reasoning("primeiro pensamento")
    await sink.aclose()
    sink.reasoning("primeiro pensamento, agora completo")
    await sink.aclose()

    assert len(client.sent) == 1
    assert "🧠 pensando" in client.sent[0]
    assert "primeiro pensamento" in client.sent[0]
    assert client.edits[-1][1].endswith("agora completo")
    assert len(client.sent) == 1  # so a primeira publicacao cria mensagem


@pytest.mark.asyncio
async def test_thinking_sink_corta_texto_longo_e_nao_quebra_com_falha():
    import logging

    from gaia.telegram.thinking import ThinkingSink

    class ClienteRuim(_FakeThinkingClient):
        async def send_message(self, text, chat_id=None, **kwargs):
            raise RuntimeError("telegram fora do ar")

    sink = ThinkingSink(ClienteRuim(), logging.getLogger("t"), 42, min_interval=0.0)
    sink.reasoning("x" * 9000)
    await sink.aclose()  # nao levanta

    client = _FakeThinkingClient()
    sink = ThinkingSink(client, logging.getLogger("t"), 42, min_interval=0.0, max_chars=500)
    sink.reasoning("a" * 2000)
    await sink.aclose()
    assert len(client.sent[0]) < 1200


@pytest.mark.asyncio
async def test_edit_message_text_usa_endpoint_do_telegram():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    await client.edit_message_text(7, "pensando…")
    await http.aclose()

    request = captured[0]
    assert request.url.path == "/bot123:abc/editMessageText"
    payload = json.loads(request.content)
    assert payload["message_id"] == 7
    assert payload["text"] == "pensando…"


class _ReasoningOrchestrator:
    last_error = ""

    def __init__(self) -> None:
        self.recebeu_sink = False

    async def answer_stream(self, text, sink):
        self.recebeu_sink = True
        sink.tool("ler", "arquivo")
        sink.reasoning("estou pensando na resposta")
        sink.token("resposta final")
        return "resposta final"

    async def answer_once(self, text, speak=False, close=False):
        return "resposta sem raciocinio"


@pytest.mark.asyncio
async def test_pensando_liga_o_raciocinio_no_chat_e_usa_answer_stream():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeThinkingClient()
    orch = _ReasoningOrchestrator()
    service = TelegramService(
        cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/pensando on"}})
    assert cfg.telegram.show_reasoning is True
    assert any("raciocínio ligado" in text for text in tg.sent)

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "oi"}})
    assert orch.recebeu_sink is True
    assert any("🧠 pensando" in text for text in tg.sent)
    assert any("estou pensando" in text for text in tg.sent + [e[1] for e in tg.edits])
    assert "resposta final" in tg.sent[-1]

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/pensando off"}})
    assert cfg.telegram.show_reasoning is False
    assert any("raciocínio desligado" in text for text in tg.sent)


# ------------------------------------------------ rotina audiobook:watch -----
# A varredura da pasta de audiobooks e a rotina continua que NAO vive no
# Orchestrator (mora no servico do Telegram). O gate da tabela `routines`
# precisa ser respeitado aqui tambem, senao o checkbox do painel nao faz nada.


class _WatchOrchestrator:
    """So o que `_audiobook_scan_once` le: o store (gate) e nada mais."""

    def __init__(self, store) -> None:
        self.store = store


@pytest.mark.asyncio
async def test_varredura_de_audiobook_respeita_a_pausa(tmp_path, monkeypatch):
    import logging

    from gaia.audiobook.service import BatchResult
    from gaia.memory.store import MemoryStore

    chamadas: list[str] = []

    async def fake_process(*args, **kwargs):
        chamadas.append("varreu")
        return BatchResult()

    monkeypatch.setattr("gaia.audiobook.service.process_inbox", fake_process)

    store = MemoryStore(tmp_path / "gaia.db")
    cfg = Config()
    cfg.audiobook.inbox = str(tmp_path / "inbox")
    cfg.audiobook.outbox = str(tmp_path / "outbox")
    service = TelegramService(
        cfg,
        _WatchOrchestrator(store),
        _FakeTelegramClient(),
        None,
        TerminalUI(quiet=True),
        logging.getLogger("t"),
    )
    try:
        store.set_routine("audiobook:watch", enabled=False)
        await service._audiobook_scan_once()
        assert chamadas == []

        store.set_routine("audiobook:watch", enabled=True)
        await service._audiobook_scan_once()
        assert chamadas == ["varreu"]
    finally:
        store.close()
