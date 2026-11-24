"""Serviço que escuta o Telegram e encaminha mensagens para a Gaia."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gaia.audiobook.jobs import AudiobookError, AudiobookJob, build_audiobook
from gaia.audiobook.service import SUPPORTED, process_inbox, text_job
from gaia.config import Config, TelegramConfig
from gaia.controller.orchestrator import Orchestrator
from gaia.stt.faster_whisper import STTError, WhisperTranscriber
from gaia.telegram.client import TelegramClient, TelegramError
from gaia.telegram.thinking import ThinkingSink

# Teto do backoff do long polling. Sem isso, uma queda do Telegram vira rajada
# de requisicoes a cada 2s durante todo o incidente.
_MAX_BACKOFF = 30.0

# Chave do meta onde o offset do getUpdates sobrevive a reinicio. Sem isso o
# processo volta com offset None, o Telegram reentrega os updates nao
# confirmados das ultimas 24h e a Gaia responde de novo o que ja respondeu.
_OFFSET_META_KEY = "telegram:offset"


# Ferramentas que dão shell ou escrevem arquivo: nunca pelo chat remoto sem
# opt-in explícito (telegram.shell_access).
_SHELL_TOOLS = frozenset({"bash", "write", "edit"})


_THINKING_COMMANDS = {"/pensando", "/pensar", "/thinking", "/raciocinio", "/raciocínio"}


# Menu de comandos no botao ``/`` do chat. Alem destes, o bot responde a
# comandos livres; a lista so torna os principais visiveis e descritinhos.
_COMMANDS_MENU = [
    ("ajuda", "lista dos comandos"),
    ("status", "servidor, modelo, sessão e voz"),
    ("tokens", "consumo de tokens por turno e total"),
    ("novo", "nova conversa, contexto zerado"),
    ("modelo", "lista ou troca o modelo"),
    ("modo", "troca a persona (modo)"),
    ("buscar", "pesquisa no Second Brain"),
    ("nota", "lê uma nota do Second Brain"),
    ("tarefas", "lista/sincroniza tarefas"),
    ("metas", "painel de foco e metas"),
    ("sonhar", "gera o sonho do dia"),
    ("lembrar", "cria um lembrete"),
    ("audiobook", "narra um PDF/TXT ou texto colado"),
    ("audiobooks", "gera os audiobooks da pasta"),
    ("proativo", "liga/desliga a iniciativa"),
    ("pensando", "mostra/oculta o raciocínio"),
    ("sair", "encerra"),
]


def telegram_permission(cfg: TelegramConfig) -> Callable[[str, list[str]], str]:
    """Política de permissão para o canal do Telegram.

    O serviço sobe com ``TerminalUI(quiet=True)``; sem terminal interativo o
    prompt de permissão responde "reject" e as ferramentas que pedem
    confirmação nunca funcionariam. Esta política substitui o prompt.

    Duas travas independentes:

    1. ``auto_approve`` só vale com ``allowed_chat_id`` fixado — sem isso o bot
       adota o primeiro chat que falar com ele, e auto-aprovar daria shell a
       qualquer desconhecido.
    2. ``bash``/``write``/``edit`` continuam negados mesmo com auto_approve,
       a menos que ``shell_access`` esteja ligado. A Agenda é acessada pelas
       ferramentas ``agenda_*``, então o chat não precisa de shell.
    """
    enabled = bool(cfg.auto_approve and str(cfg.allowed_chat_id or "").strip())

    def decide(tool: str, resources: list[str]) -> str:
        if tool in _SHELL_TOOLS and not cfg.shell_access:
            return "reject"
        return "always" if enabled else "reject"

    return decide


def _safe_name(name: str) -> str:
    """Nome de arquivo vindo do Telegram: sem caminho e sem caractere perigoso."""
    clean = Path(name).name
    clean = re.sub(r"[^\w.\- ]+", "_", clean, flags=re.UNICODE).strip(" .")
    return clean or "livro.pdf"


class TelegramService:
    def __init__(
        self,
        cfg: Config,
        orchestrator: Orchestrator,
        client: TelegramClient,
        transcriber: WhisperTranscriber,
        ui: Any,
        logger: logging.Logger,
    ):
        self.cfg = cfg
        self.orchestrator = orchestrator
        self.client = client
        self.transcriber = transcriber
        self.ui = ui
        self.logger = logger
        self._chat_id: str | None = None
        self._background: set[asyncio.Task] = set()
        setter = getattr(getattr(orchestrator, "client", None), "set_audiobook_watch", None)
        if callable(setter):
            setter(self._watch_audiobook)

    async def run(self) -> None:
        username = "?"
        try:
            me = await self.client.get_me()
            username = me.get("username", "?") if isinstance(me, dict) else "?"
        except TelegramError as exc:
            # Um 502/timeout aqui nao pode matar o processo: o KeepAlive do
            # launchd reiniciaria em loop e o bot ficaria mudo ate a API voltar.
            # O loop de getUpdates abaixo ja trata falha com backoff.
            self.logger.warning("getMe falhou no boot: %s", exc)
        try:
            await self.client.set_my_commands(_COMMANDS_MENU)
        except TelegramError as exc:
            # Se falhar (rede/API), nao derruba o boot: o bot continua, so
            # sem o menu; a proxima subida tenta de novo.
            self.logger.warning("setMyCommands falhou: %s", exc)
        self.ui.info(f"Telegram conectado: @{username}")
        self.ui.hint("enviando mensagens; Ctrl+C encerra")
        # Briefings diarios rodam mesmo sem mensagem recebida.
        self.orchestrator.start_briefing()
        # A rotina semanal de vagas tambem: sem isso ela so acordaria depois
        # da primeira mensagem, e a segunda-feira passaria em branco.
        self.orchestrator.start_career()
        # Sem isso o servico responde mas nunca dispara lembrete nem aviso de
        # agenda: o laco proativo so existia no caminho da TUI.
        self.orchestrator.start_proactive()
        # Consolidacao das 23:00 e sonho das 02:00 tambem precisam subir sem
        # mensagem: headless, ninguem fala com ela de madrugada.
        self.orchestrator.start_memory()
        # Este servico e o dono do poller: proatividade vai para o chat.
        self.orchestrator.owns_telegram = True
        if self.cfg.audiobook.enabled and self.cfg.audiobook.watch_minutes > 0:
            watcher = asyncio.create_task(self._audiobook_watcher())
            self._background.add(watcher)
            watcher.add_done_callback(self._background.discard)
        offset: int | None = self._load_offset()
        backoff = 2.0
        conflict_warned = False
        while True:
            try:
                updates = await self.client.get_updates(offset=offset)
                backoff = 2.0
                if conflict_warned:
                    conflict_warned = False
                    self.logger.info("getUpdates voltou a funcionar")
            except TelegramError as exc:
                # 409 Conflict tem causa certa e acionavel; o aviso completo
                # so sai uma vez por sequencia para nao inundar o journal.
                if "Conflict" in str(exc) and not conflict_warned:
                    conflict_warned = True
                    self.logger.warning(
                        "getUpdates recusado: outro processo esta usando o mesmo bot "
                        "token. Rode `ps aux | grep 'gaia telegram serve'` no Mac e no "
                        "mini e deixe so um: %s",
                        exc,
                    )
                self.logger.warning("getUpdates falhou: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            for update in updates:
                offset = int(update.get("update_id", 0)) + 1
                try:
                    await self._handle_update(update)
                except Exception as exc:  # pragma: no cover - depende da rede/agente
                    self.logger.exception("falha ao processar update")
                    await self._safe_reply(update, f"Deu erro aqui: {exc}")
                # O update ja foi respondido (ou virou mensagem de erro):
                # confirma-lo evita responder duas vezes depois de um restart.
                self._save_offset(offset)
            await asyncio.sleep(0.05)

    def _load_offset(self) -> int | None:
        """Retoma de onde parou; sem valor salvo, comeca do pendente."""
        store = getattr(self.orchestrator, "store", None)
        if store is None:
            return None
        try:
            raw = store.get_meta(_OFFSET_META_KEY)
        except Exception:
            self.logger.debug("falha ao ler o offset salvo", exc_info=True)
            return None
        try:
            return int(str(raw)) if raw else None
        except (TypeError, ValueError):
            self.logger.warning("offset salvo invalido: %r", raw)
            return None

    def _save_offset(self, offset: int) -> None:
        store = getattr(self.orchestrator, "store", None)
        if store is None:
            return
        try:
            store.set_meta(_OFFSET_META_KEY, str(offset))
        except Exception:
            # Perder a marca so custa um replay depois; nao pode matar o laco.
            self.logger.debug("falha ao salvar o offset", exc_info=True)

    def _allowed(self, chat_id: Any) -> bool:
        allowed = str(self.cfg.telegram.allowed_chat_id or "").strip()
        if allowed:
            return str(chat_id) == allowed
        if self._chat_id is None:
            self._chat_id = str(chat_id)
            self.ui.info(f"chat autorizado automaticamente: {self._chat_id}")
        return str(chat_id) == self._chat_id

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is None or not self._allowed(chat_id):
            return
        # Espelhar o canal: entrou audio, sai audio. `voice_replies` continua
        # servindo para responder em voz tambem quando a entrada e texto.
        was_voice = bool(message.get("voice") or message.get("audio"))
        document = message.get("document") or {}
        if document.get("file_id"):
            await self._handle_document(document, str(message.get("caption") or ""), chat_id)
            return
        text = (message.get("text") or "").strip()
        if text.partition(" ")[0].lower() in {"/audiobook", "/audiolivro"}:
            if text.partition(" ")[2].strip():
                await self._handle_audiobook_text(text.partition(" ")[2].strip(), chat_id)
                return
        if text.lower() in {"/audiobooks", "/audiobooks gerar", "/audiolivros"}:
            await self._generate_inbox(chat_id)
            return
        if text in {"/start", "/iniciar"}:
            await self.client.send_message(
                "Oi! Sou a Gaia. Manda texto ou áudio que eu executo e te aviso "
                "aqui quando terminar. Se quiser começar do zero, manda /novo.",
                chat_id=chat_id,
            )
            return
        if text.lower() in {"/novo", "/nova", "/novo chat", "/nova sessao", "/nova sessão"}:
            await self._start_new_session(chat_id)
            return
        if text == "/modelo" or text.startswith("/modelo ") or text in {"/modelos", "/model"}:
            await self._handle_model_command(text, chat_id)
            return
        if text.partition(" ")[0].lower() in _THINKING_COMMANDS:
            await self._handle_thinking_command(text, chat_id)
            return
        if not text:
            text = await self._transcribe_message(message)
        if not text:
            return

        await self.client.send_chat_action(chat_id, "typing")
        await self.client.send_message("⏳ trabalhando nisso…", chat_id=chat_id)
        sink = (
            ThinkingSink(self.client, self.logger, chat_id)
            if self.cfg.telegram.show_reasoning
            else None
        )
        try:
            stream = getattr(self.orchestrator, "answer_stream", None)
            if sink is not None and callable(stream):
                answer = await stream(text, sink)
            else:
                answer = await self.orchestrator.answer_once(text, speak=False, close=False)
        except Exception as exc:
            self.logger.exception("falha no agente")
            answer = f"Deu erro aqui: {exc}"
        finally:
            if sink is not None:
                await sink.aclose()

        if answer:
            await self.client.send_message(answer, chat_id=chat_id)
            if was_voice or self.cfg.telegram.voice_replies:
                await self._send_voice(answer, chat_id)
        elif self.orchestrator.last_error:
            await self.client.send_message(
                f"Não consegui responder: {self.orchestrator.last_error}", chat_id=chat_id
            )
        else:
            await self.client.send_message("Concluído.", chat_id=chat_id)

    async def _start_new_session(self, chat_id: Any) -> None:
        """`/novo` no Telegram = mesma coisa que `/novo` no terminal."""
        create = getattr(self.orchestrator, "new_session", None)
        if create is None:
            await self.client.send_message(
                "Esse canal não sabe criar sessão nova.", chat_id=chat_id
            )
            return
        try:
            await create()
        except Exception as exc:
            self.logger.exception("falha ao criar sessão")
            await self.client.send_message(f"Não consegui criar: {exc}", chat_id=chat_id)
            return
        await self.client.send_message("Pronto, sessão nova. Contexto zerado.", chat_id=chat_id)

    async def _handle_thinking_command(self, text: str, chat_id: Any) -> None:
        """`/pensando [on|off]` alterna o raciocinio visivel neste chat."""
        argument = text.partition(" ")[2].strip().lower()
        if argument in {"on", "ligar", "ligado", "sim", "1"}:
            self.cfg.telegram.show_reasoning = True
        elif argument in {"off", "desligar", "desligado", "nao", "não", "0"}:
            self.cfg.telegram.show_reasoning = False
        else:
            self.cfg.telegram.show_reasoning = not self.cfg.telegram.show_reasoning
        if self.cfg.telegram.show_reasoning:
            await self.client.send_message(
                "🧠 raciocínio ligado: mando uma mensagem que vai sendo editada "
                "enquanto eu penso. /pensando off desliga.",
                chat_id=chat_id,
            )
        else:
            await self.client.send_message(
                "🧠 raciocínio desligado: volto a mandar só a resposta."
                " /pensando liga de novo.",
                chat_id=chat_id,
            )

    async def _handle_model_command(self, text: str, chat_id: Any) -> None:
        """`/modelo` no Telegram: lista numerada ou troca pelo número/slug."""
        switch = getattr(self.orchestrator, "model_messages", None)
        if not callable(switch):
            await self.client.send_message(
                "Esse canal não sabe trocar de modelo.", chat_id=chat_id
            )
            return
        argument = text.partition(" ")[2].strip()
        if text.strip() in {"/modelos", "/model"} and not argument:
            argument = ""
        try:
            messages = await switch(argument)
        except Exception as exc:
            self.logger.exception("falha ao trocar modelo")
            await self.client.send_message(
                f"Não consegui trocar o modelo: {exc}", chat_id=chat_id
            )
            return
        lines = [body for kind, body in messages if kind != "info" or body]
        await self.client.send_message("\n".join(lines) or "ok", chat_id=chat_id)

    async def _transcribe_message(self, message: dict[str, Any]) -> str:
        media = message.get("voice") or message.get("audio")
        if not media:
            return ""
        file_id = media.get("file_id")
        if not file_id:
            return ""
        self.ui.info("baixando áudio…")
        file_path = await self.client.get_file(str(file_id))
        data = await self.client.download_file(file_path)
        mime = str(media.get("mime_type") or "")
        if "ogg" in mime:
            suffix = ".ogg"
        elif "m4a" in mime or "mp4" in mime:
            suffix = ".m4a"
        else:
            suffix = ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            self.ui.info(f"transcrevendo áudio ({len(data)} bytes)…")
            return await asyncio.to_thread(self.transcriber.transcribe_file, tmp)
        except STTError as exc:
            self.logger.warning("transcrição falhou: %s", exc)
            return ""
        finally:
            tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------ audiobooks
    async def _audiobook_watcher(self) -> None:
        """audiobook.watch_minutes > 0: confere a pasta sozinho de tempos em tempos."""
        cfg = self.cfg.audiobook
        interval = max(1.0, cfg.watch_minutes) * 60
        while True:
            await asyncio.sleep(interval)
            try:
                await self._audiobook_scan_once()
            except Exception:
                self.logger.exception("varredura de audiobooks falhou")

    async def _audiobook_scan_once(self) -> None:
        """Uma varredura: gate do painel, processa a inbox e avisa no chat.

        Separado do laco para o gate ser testavel sem ``sleep``. A rotina
        ``audiobook:watch`` pausada no painel nao varre nem avisa.
        """
        from gaia.audiobook.service import process_inbox
        from gaia.control import routines as routines_mod

        cfg = self.cfg.audiobook
        store = getattr(self.orchestrator, "store", None)
        if not routines_mod.is_enabled(store, "audiobook:watch"):
            return
        inbox = self.cfg.audiobook_inbox()
        outbox = self.cfg.audiobook_outbox()
        tmp_dir = cfg.tmp_path(self.cfg.resolved_data_dir())
        result = await process_inbox(cfg, inbox, outbox, tmp_dir, self.logger)
        chat_id = self.cfg.telegram.allowed_chat_id or self._chat_id
        if result.empty or not cfg.notify or not chat_id:
            return
        try:
            await self.client.send_message(
                "📚 audiobooks prontos em " + str(outbox) + "\n" + result.summary(),
                chat_id=chat_id,
            )
        except TelegramError as exc:
            self.logger.debug("aviso de audiobook falhou: %s", exc)

    async def _handle_document(self, document: dict[str, Any], caption: str, chat_id: Any) -> None:
        """PDF/TXT enviado no chat: salva na pasta de entrada e narra."""
        name = str(document.get("file_name") or "livro")
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED:
            await self.client.send_message(
                "Só narro PDF ou texto (.txt/.md). Esse arquivo não abre.",
                chat_id=chat_id,
            )
            return
        cfg = self.cfg.audiobook
        inbox = self.cfg.audiobook_inbox()
        inbox.mkdir(parents=True, exist_ok=True)
        await self.client.send_chat_action(chat_id, "upload_document")
        data = await self.client.download_file(await self.client.get_file(str(document["file_id"])))
        target = inbox / _safe_name(name)
        target.write_bytes(data)
        title = caption.strip() or target.stem
        await self.client.send_message(
            f"📖 recebi {target.name} ({len(data) // 1024} KB). Gerando o audiobook "
            f"“{title}” — te aviso quando terminar.",
            chat_id=chat_id,
        )
        job = AudiobookJob(
            source=target,
            outbox=self.cfg.audiobook_outbox(),
            cfg=cfg,
            tmp_dir=cfg.tmp_path(self.cfg.resolved_data_dir()),
            title=title,
        )
        await self._run_audiobook(job, chat_id, archive_to=inbox / "processados")

    async def _handle_audiobook_text(self, text: str, chat_id: Any) -> None:
        if len(text) < 200:
            await self.client.send_message(
                "Texto curto demais para um audiobook. Mande um PDF ou um texto "
                "maior: /audiobook <texto>",
                chat_id=chat_id,
            )
            return
        cfg = self.cfg.audiobook
        job = text_job(
            cfg,
            self.cfg.audiobook_outbox(),
            cfg.tmp_path(self.cfg.resolved_data_dir()),
            text,
        )
        await self.client.send_message(
            f"📖 narrando o texto colado ({len(text)} caracteres)…", chat_id=chat_id
        )
        await self._run_audiobook(job, chat_id)

    async def _generate_inbox(self, chat_id: Any) -> None:
        inbox = self.cfg.audiobook_inbox()
        outbox = self.cfg.audiobook_outbox()
        cfg = self.cfg.audiobook
        if not cfg.enabled:
            await self.client.send_message("Audiobook desligado.", chat_id=chat_id)
            return
        await self.client.send_message(f"🔎 conferindo {inbox}…", chat_id=chat_id)
        result = await process_inbox(
            cfg,
            inbox,
            outbox,
            cfg.tmp_path(self.cfg.resolved_data_dir()),
            self.logger,
            progress=None,
        )
        if result.empty:
            await self.client.send_message(
                f"Nenhum arquivo novo em {inbox}. Coloque PDFs/TXT nessa pasta e "
                "mande /audiobooks de novo.",
                chat_id=chat_id,
            )
            return
        await self.client.send_message(
            "Audiobooks prontos em " + str(outbox) + "\n" + result.summary(),
            chat_id=chat_id,
        )

    async def _run_audiobook(self, job: AudiobookJob, chat_id: Any, archive_to: Path | None = None) -> None:
        """Narra em segundo plano com progresso editando uma mensagem."""
        message = await self.client.send_message(
            f"🎙️ {job.title}: preparando…", chat_id=chat_id
        )
        message_id = message.get("message_id") if isinstance(message, dict) else None
        last = {"at": 0.0}

        async def edit(text: str) -> None:
            if message_id is None:
                return
            try:
                await self.client.edit_message_text(message_id, text, chat_id=chat_id)
            except TelegramError as exc:
                self.logger.debug("progresso do audiobook falhou: %s", exc)

        def progress(done: int, total: int) -> None:
            # Edit a cada 3s: o Telegram limita edicoes de uma mesma mensagem e o
            # progresso por trecho e rapido demais para caber no limite.
            now = time.monotonic()
            if done < total and now - last["at"] < 3.0:
                return
            last["at"] = now
            percent = int(done * 100 / total) if total else 0
            edit_task = asyncio.create_task(
                edit(f"🎙️ {job.title}: {percent}% ({done}/{total} trechos)")
            )
            edit_task.add_done_callback(lambda t: t.exception())

        await self.client.send_chat_action(chat_id, "record_voice")
        try:
            output = await build_audiobook(job, progress=progress)
        except AudiobookError as exc:
            await edit(f"❌ {job.title}: {exc}")
            if archive_to is not None and job.source.exists():
                archive_to.mkdir(parents=True, exist_ok=True)
                job.source.replace(archive_to / job.source.name)
            return
        except Exception as exc:
            self.logger.exception("audiobook falhou")
            await edit(f"❌ {job.title}: erro inesperado ({exc})")
            return
        size_mb = max(1, output.stat().st_size // (1024 * 1024))
        await edit(f"✅ {job.title} pronto ({size_mb} MB). Enviando…")
        await self._deliver_audiobook(output, job, chat_id)

    async def _deliver_audiobook(self, output: Path, job: AudiobookJob, chat_id: Any) -> None:
        caption = f"📖 {job.title} · narrado por Gaia · {len(job.chapters)} faixa(s)"
        if output.stat().st_size > 49 * 1024 * 1024:
            caption += "\n(ultrapassa o limite do Telegram; arquivo salvo na pasta de saída)"
            await self.client.send_message(caption, chat_id=chat_id)
            return
        try:
            await self.client.send_audio(output, chat_id=chat_id, caption=caption)
        except TelegramError as exc:
            self.logger.warning("envio do audiobook falhou: %s", exc)
            await self.client.send_message(
                f"Não consegui enviar o MP3 ({exc}). Ele está em {output}", chat_id=chat_id
            )

    def _watch_audiobook(self, task: asyncio.Task, job: AudiobookJob) -> None:
        """Ferramenta `audiobook` iniciou a narracao: entrega o MP3 ao terminar."""

        async def deliver() -> None:
            chat_id = self.cfg.telegram.allowed_chat_id or self._chat_id
            try:
                output = await task
            except Exception as exc:
                if chat_id:
                    await self.client.send_message(f"❌ {job.title}: {exc}", chat_id=chat_id)
                return
            if chat_id:
                await self._deliver_audiobook(output, job, chat_id)

        task = asyncio.create_task(deliver())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _send_voice(self, text: str, chat_id: Any) -> None:
        prepared = await self.orchestrator.tts.prepare(text)
        if prepared is None:
            return
        backend, path = prepared
        try:
            await self.client.send_audio(path, chat_id=chat_id, as_voice=True)
        finally:
            backend.cleanup(path)

    async def _safe_reply(self, update: dict[str, Any], text: str) -> None:
        chat_id = ((update.get("message") or {}).get("chat") or {}).get("id")
        if chat_id is None:
            return
        try:
            await self.client.send_message(text, chat_id=chat_id)
        except Exception as exc:
            self.logger.debug("falha ao responder no Telegram: %s", exc)
