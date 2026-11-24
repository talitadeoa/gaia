"""Orquestrador da Gaia: voz -> STT -> contexto -> agente -> resposta/voz.

O comportamento concreto vive em mixins coesos (fala, turnos, voz, comandos,
registro e iniciativa). Esta classe mantem o estado compartilhado, o ciclo de
vida (bootstrap/shutdown) e a sessao/conversa ativa.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from gaia.agenda.client import AgendaClient
from gaia.agent import AgentClient, AgentError
from gaia.agent.session import SessionPolicy
from gaia.agent.types import SessionInfo
from gaia.audio.recorder import ContinuousListener, Recorder
from gaia.bots import Bot, BotIndisponivel, bot_problem, load_bots, require_bot
from gaia.bots.bus import BotBus
from gaia.config import Config
from gaia.controller.backlog import BacklogMixin
from gaia.controller.bots import BotsMixin
from gaia.controller.briefing import BriefingMixin
from gaia.controller.career import CareerMixin
from gaia.controller.commands import CommandsMixin
from gaia.controller.contract import StreamSink
from gaia.controller.goals import GoalTracker
from gaia.controller.initiative import InitiativeMixin
from gaia.controller.memory import MemoryMixin
from gaia.controller.modes import (
    Persona,
    get_persona,
    persona_from_bot,
    resolve_mode,
    stored_mode_keys,
)
from gaia.controller.registration import RegistrationMixin
from gaia.controller.routines import RoutineMixin
from gaia.controller.scheduler import SchedulerMixin
from gaia.controller.speech import SpeechMixin
from gaia.controller.turns import TurnMixin
from gaia.controller.voice import VoiceMixin
from gaia.estudos.client import EstudosClient
from gaia.finance.watchlist import Watchlist
from gaia.focus import FocusStore
from gaia.interface.terminal import TerminalUI
from gaia.memory.store import MemoryStore
from gaia.secondbrain.index import SecondBrainIndex
from gaia.secondbrain.notes import SecondBrain
from gaia.stt.faster_whisper import WhisperTranscriber
from gaia.tasks.store import TaskStore
from gaia.telegram.client import TelegramClient
from gaia.tts.manager import TTSManager


class Orchestrator(
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
    SchedulerMixin,
    BacklogMixin,
    BotsMixin,
):

    def __init__(
        self,
        cfg: Config,
        logger: logging.Logger,
        ui: TerminalUI,
        store: MemoryStore,
        index: SecondBrainIndex,
        brain: SecondBrain,
        tts: TTSManager,
        transcriber: WhisperTranscriber,
        recorder: Recorder,
        client: AgentClient,
        channel: str = "cli",
    ):
        self.cfg = cfg
        self.logger = logger
        self.ui = ui
        self.store = store
        self.index = index
        self.brain = brain
        self.tts = tts
        self.transcriber = transcriber
        self.recorder = recorder
        self.client = client
        self.telegram = TelegramClient(cfg.telegram)
        self.directory = cfg.project_directory()
        # Canal de origem (cli, telegram, ...). Cada canal tem conversas e
        # historico proprios: mesma pasta + mesmo modo nao basta para dividir
        # o fio, senao o terminal e o Telegram conversam entre si.
        self.channel = channel
        # Quem e dono do poller do Telegram entrega a proatividade no chat.
        # Sem isso, o `gaia serve` (channel="serve") mandava lembrete para o
        # TTS de uma maquina headless e ninguem recebia nada.
        self.owns_telegram = channel == "telegram"
        self._adopt_legacy_history()
        # Registry dos bots (default do codigo + `[bots.<id>]` do TOML). O que
        # estiver mal configurado no TOML levanta aqui, no boot: um bot que
        # nao consegue cumprir a allowlist/escopo nao sobe "meio ligado".
        self.bots = load_bots(cfg)
        self.persona = resolve_mode(
            store.get_meta(self._mode_meta_key()) or cfg.interface.default_mode
        )

        self.conversation_id: int | None = None
        self.session_id: str = ""
        self._voice_ok: bool | None = None
        self._voice_msg = ""
        self._prefs_cache = ""
        self._prefs_mtime: float = -1.0
        self._last_activity = time.monotonic()
        self._busy = False
        self._restart_requested = False
        self._last_error = ""
        self._recording = False
        self._listening = False
        self._voice_mode = bool(cfg.audio.continuous)
        self._pending_input = ""
        self._stream_sink = None
        self._mic_muted = False
        self._continuous_hint_shown = False
        self._permission_active = False
        self._last_listen_at = time.monotonic()
        self._listener: ContinuousListener | None = None
        self._speaking_event = asyncio.Event()
        self._speaking_event.set()
        self._stt_lock = asyncio.Lock()
        self._partial_last = ""
        self._proactive_task: asyncio.Task | None = None
        self._memory_task: asyncio.Task[None] | None = None
        self._briefing_task: asyncio.Task[None] | None = None
        self._briefing_last_attempt = 0.0
        self._market_last_attempt = 0.0
        self._dream_report_last_attempt = 0.0
        self._career_task: asyncio.Task[None] | None = None
        self._career_last_attempt = 0.0
        self._scheduler_task: asyncio.Task[None] | None = None
        # Fila do backlog (card do web): um item por vez, estado no banco.
        self._backlog_worker = None
        self._retry_plan: dict[str, tuple[float, int]] = {}
        # Bus entre bots (`falar_com_bot`): criado no boot, com o loop do motor.
        self._bot_bus: BotBus | None = None
        self._proactive_count = 0
        self._proactive_day = datetime.now().astimezone().date()
        self._last_proactive = 0.0
        self._last_idle_message = ""
        self._initiative_session_id: str | None = None
        self._task_session_id: str | None = None
        self._echo_sessions: dict[str, str] = {}
        self._echo_cache: dict[str, dict[str, str]] = {}
        self._tasks_next_check = 0.0
        self._task_page_updated_at: str | None = None
        self._agenda_pending_names: list[str] = []
        self.goals = GoalTracker()
        self.watchlist = Watchlist()
        self.focus = FocusStore(cfg.focus.path)
        self.tasks = TaskStore()
        self.agenda = AgendaClient(cfg.agenda)
        self.estudos = EstudosClient(cfg.estudos)
        self._agenda_next_check = 0.0
        self._estudos_next_check = 0.0
        self._agenda_seen: set[str] = set()
        self._agenda_day = None
        self._tts_lock = asyncio.Lock()
        self._audiobook_lock = asyncio.Lock()
        self._user_name_cache: str | None = None
        self._warmup_task: asyncio.Task | None = None
        self._speech_active = False
        self._speech_buffer = ""
        self._speech_pending = ""
        self._speech_queue: asyncio.Queue[str | None] | None = None
        self._speech_task: asyncio.Task | None = None
        self._speech_fed_chars = 0
        self._answer_buffer = ""
        self._session_dirty = False
        self._last_assistant_message_id: str | None = None
        # Serializa troca de modo/conversa/sessao. Sem isso, dois workers do TUI
        # podem intercalar os awaits e gravar a sessao de um modo na conversa
        # de outro.
        self._transition_lock = asyncio.Lock()
        # Historico local reinjetado no proximo turno quando a sessao antiga
        # nao existe mais (a conversa continua, o contexto do provedor nao).
        self._resume_context = ""

    def _mode_meta_key(self) -> str:
        """Chave do meta onde o modo ativo fica salvo, por canal."""
        return "mode" if self.channel == "cli" else f"mode@{self.channel}"

    def _adopt_legacy_history(self) -> None:
        """Traz conversas do nome antigo do repositório (zora) para o atual."""
        legacy = self.directory.parent / "zora"
        if legacy == self.directory:
            return
        try:
            if self.store.project_has_turns(str(self.directory)):
                return
            if not self.store.project_has_turns(str(legacy)):
                return
            moved = self.store.rehome_conversations(str(legacy), str(self.directory))
            if not moved:
                return
            self.logger.info(
                "histórico de %s adotado em %s (%d conversas)",
                legacy,
                self.directory,
                moved,
            )
            mode = self.store.richest_mode(str(self.directory), self.channel)
            if mode:
                self.store.set_meta(self._mode_meta_key(), mode)
        except Exception:
            self.logger.exception("falha ao adotar o histórico antigo")

    async def _bootstrap(self) -> None:
        await self.client.ensure_ready()
        # A ferramenta `criar_automacao` precisa do banco e da config do
        # processo: o AgentClient nasce antes do store no bootstrap, entao a
        # ligacao acontece aqui (uma vez, idempotente).
        ligar = getattr(self.client, "set_memory_store", None)
        if callable(ligar):
            ligar(self.store)
        # Bus entre bots: `falar_com_bot` so entra no catalogo com ele ligado.
        # O bus precisa do Orchestrator (que nasce depois do AgentClient), entao
        # a ligacao tambem acontece aqui, uma vez.
        self.start_bot_bus()
        if self.cfg.agent.auto_approve:
            self.ui.warning(
                "auto-aprovação de ferramentas do agente está ATIVA (agent.auto_approve=true)"
            )
        self._prepare_conversation()
        assert self.conversation_id is not None
        await self._activate_conversation(self.conversation_id, self.persona)
        await self._seed_last_message_id()
        self._refresh_index_on_start()
        if self._voice_available():
            self._warmup_task = asyncio.create_task(self._warmup_stt())

    def _start_background_workers(self) -> None:
        self._start_memory_worker()
        # Briefing, carteira, vagas, memoria e sonho obedecem a agenda da
        # tabela `automations`; quem dispara e o agendador unico.
        self.start_scheduler()
        # Fila do backlog do painel: um item por vez, estado no banco. Ligar
        # aqui (e nao no clique) faz um item enfileirado antes do restart
        # continuar depois dele.
        self.start_backlog()
        self.start_bot_bus()

    def start_bot_bus(self) -> BotBus | None:
        """Liga `falar_com_bot` (idempotente; precisa do loop do motor).

        Sem o bus a ferramenta nao existe: o catalogo do processo continua com
        as outras, e a allowlist de cada bot decide quem tem a de falar. O
        `bind_loop` usa o loop em que este metodo roda (o do motor).
        """
        bus = getattr(self, "_bot_bus", None)
        if bus is None:
            bus = BotBus(self)
            self._bot_bus = bus
        try:
            bus.bind_loop(asyncio.get_running_loop())
        except RuntimeError:  # chamado fora do loop (teste/boot parcial)
            pass
        ligar = getattr(self.client, "set_bot_bus", None)
        if callable(ligar):
            ligar(bus)
        return bus

    def start_briefing(self) -> None:
        """Liga o briefing sem depender do bootstrap do agente."""
        self._start_briefing_worker()

    def start_memory(self) -> None:
        """Liga consolidacao e sonho sem depender de uma mensagem recebida.

        No servico do Telegram os workers so subiam no primeiro ``answer_once``:
        um mini reiniciado a noite ficava sem consolidar as 23:00 e sem sonhar
        as 02:00 ate alguem falar com a Gaia.
        """
        self._start_memory_worker()

    def start_career(self) -> None:
        """Liga a rotina de vagas sem depender do bootstrap do agente."""
        self._start_career_worker()

    def start_automations(self) -> None:
        """Liga o agendador unico (briefing, carteira, vagas, memoria, sonho)."""
        self.start_scheduler()

    def start_proactive(self) -> None:
        """Liga lembretes, agenda e foco sem a TUI (servico do Telegram).

        Sem isso o servico responde mas nunca dispara um lembrete: o laco
        proativo so era criado em ``run()``, que a TUI usa.
        """
        if not self.cfg.proactive.enabled:
            return
        if self._proactive_task is None or self._proactive_task.done():
            self._proactive_task = asyncio.create_task(self._proactive_loop(greet=False))

    def set_proactive_enabled(self, enabled: bool) -> bool:
        """Liga/desliga a iniciativa proativa no processo (tela de automacoes).

        O `/proativo` da TUI e o checkbox do web precisam do mesmo efeito:
        desligar para o laco agora (sem esperar o proximo ciclo) e religar
        criando a tarefa de novo. Devolve se o laco esta rodando depois da
        chamada; **precisa** ser chamado no loop do motor (create_task).
        """
        self.cfg.proactive.enabled = bool(enabled)
        if not enabled:
            tarefa = self._proactive_task
            self._proactive_task = None
            if tarefa is not None:
                tarefa.cancel()
            return False
        if self._proactive_task is None or self._proactive_task.done():
            self._proactive_task = asyncio.create_task(
                self._proactive_loop(greet=False)
            )
        return True

    async def _warmup_stt(self) -> None:
        try:
            await asyncio.to_thread(self.transcriber.warmup)
        except Exception as exc:  # pragma: no cover
            # Um modelo indisponivel nao deve passar despercebido: sem isso o
            # primeiro 'falar' cai num generico "nao entendi".
            self.logger.warning("STT indisponivel: %s", exc)

    async def _shutdown(self) -> None:
        if self._memory_task is not None:
            if not self._memory_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._memory_task), timeout=15.0
                    )
                except Exception:
                    self._memory_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self._memory_task
            self._memory_task = None
        if self._briefing_task is not None:
            self._briefing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._briefing_task
            self._briefing_task = None
        await self.stop_backlog()
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._scheduler_task
            self._scheduler_task = None
        if self._career_task is not None:
            self._career_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._career_task
            self._career_task = None
        if (
            self.cfg.memory.enabled
            and self.cfg.memory.on_exit
            and datetime.now().astimezone().strftime("%H:%M") >= self.cfg.memory.at
        ):
            try:
                await asyncio.wait_for(
                    self.consolidate_day(
                        datetime.now().astimezone().date(), timeout_seconds=20.0
                    ),
                    timeout=25.0,
                )
            except Exception as exc:  # pragma: no cover - depende do provedor
                self.logger.warning("consolidacao ao sair falhou: %s", exc)
        if self._warmup_task is not None:
            self._warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._warmup_task
            self._warmup_task = None
        await self._stop_speech_streaming()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._proactive_task is not None:
            self._proactive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._proactive_task
            self._proactive_task = None
        self.tts.stop()
        self.ui.close()
        await self.telegram.aclose()
        await self.client.aclose()

    async def run_once(self, text: str) -> None:
        """Executa uma unica instrucao (util para `gaia ask`)."""
        await self._bootstrap()
        try:
            raw = text.strip()
            if not raw:
                return
            if raw.startswith("/"):
                await self._handle_command(raw)
                return
            assert self.conversation_id is not None
            self.store.add_turn(self.conversation_id, "user", raw)
            await self._dispatch(raw)
        finally:
            await self._shutdown()

    async def answer_once(
        self, text: str, speak: bool = False, close: bool = True
    ) -> str:
        """Responde uma instrução externa (ex.: Telegram) e devolve o texto."""
        if not close:
            self._start_background_workers()
        if not self.session_id:
            await self._bootstrap()
        try:
            raw = text.strip()
            if not raw:
                return ""
            if raw.startswith("/"):
                await self._handle_command(raw)
                return ""
            assert self.conversation_id is not None
            self.store.add_turn(self.conversation_id, "user", raw)
            return await self._dispatch(raw) if speak else await self._dispatch_silent(raw)
        finally:
            if close:
                await self._shutdown()

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    @property
    def last_error(self) -> str:
        """Ultimo erro do turno; o Telegram mostra isso em vez de 'Concluido.'."""
        return self._last_error

    async def prepare(self) -> None:
        """Sobe servidor/sessão antes do primeiro uso (aquece a TUI)."""
        if not self.session_id:
            await self._bootstrap()
        self._start_background_workers()

    async def answer_stream(self, text: str, sink: StreamSink | None) -> str:
        """Como answer_once, mas envia tokens/eventos para um sink de UI."""
        self._stream_sink = sink
        try:
            return await self.answer_once(text, speak=False, close=False)
        finally:
            self._stream_sink = None

    async def speak_text(self, text: str) -> None:
        await self._speak(text)

    async def aclose(self) -> None:
        await self._shutdown()

    async def start_services(self) -> None:
        """Sobe tudo menos o loop interativo.

        `gaia serve` usa isto: o motor (bootstrap, workers de memória, briefing,
        carreira, proatividade) precisa rodar sem ninguém lendo teclado. A TUI
        chama `start()`, que é isto mais o `_loop()`.
        """
        await self._bootstrap()

        if self.cfg.interface.show_banner:
            self.ui.banner(
                project=self.directory,
                session_id=self.session_id,
                model=self.cfg.agent.model_slug(),
                sb_notes=self.index.count(),
                voice=self.tts.provider,
                tts_enabled=self.tts.enabled,
            )
        else:
            self.ui.status(
                self.directory,
                self.session_id,
                self.cfg.agent.model_slug(),
                self.index.count(),
                self.tts.provider,
            )

        if self.cfg.interface.show_metas_on_start:
            try:
                await self._show_dashboard()
            except Exception as exc:  # pragma: no cover
                self.logger.warning("painel de metas: %s", exc, exc_info=True)

        if self.cfg.proactive.enabled and self.cfg.proactive.startup_greeting:
            if self._routine_on("initiative:greeting"):
                try:
                    await self._startup_greeting()
                except Exception as exc:  # pragma: no cover
                    self.logger.warning(
                        "falha na saudação inicial: %s", exc, exc_info=True
                    )

        if self.cfg.proactive.enabled:
            self._proactive_task = asyncio.create_task(self._proactive_loop(greet=False))

        self._start_background_workers()

        if self._routine_on("initiative:focus"):
            try:
                await self._check_focus(datetime.now().astimezone())
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha ao checar foco: %s", exc, exc_info=True)

    async def start(self) -> None:
        await self.start_services()

        if not (self.cfg.audio.continuous and self._voice_available()):
            self.ui.hint(
                "Digite para texto · Enter vazio ou /fala volta à voz · "
                "/ajuda · Ctrl+C interrompe"
            )

        try:
            await self._loop()
        finally:
            await self._shutdown()

    def _prepare_conversation(self) -> None:
        conv = self.store.get_or_create_conversation(
            str(self.directory), self.persona.key, self.channel
        )
        self.conversation_id = int(conv["id"])

    def _refresh_index_on_start(self) -> None:
        if not self.cfg.secondbrain.path:
            return
        if not self.brain.root.exists():
            self.ui.warning(
                f"Second Brain não encontrado em {self.brain.root} (use /indexar após configurar)"
            )
            return
        if not (self.cfg.secondbrain.reindex_on_start or self.index.count() == 0):
            return
        try:
            stats = self.index.reindex()
            if stats["indexed"] or stats["removed"]:
                self.ui.info(
                    f"Second Brain indexado: {stats['indexed']} nova(s), "
                    f"{stats['removed']} removida(s), {stats['total']} no total"
                )
        except Exception as exc:  # pragma: no cover - depende do FS
            self.logger.warning("falha ao indexar Second Brain: %s", exc, exc_info=True)
            self.ui.warning(f"não foi possível indexar o Second Brain: {exc}")

    async def _open_session(
        self, conversation_id: int, persona: Persona, bot: Bot | None = None
    ) -> tuple[str, bool]:
        """Retoma a sessao da conversa ou cria uma nova.

        Devolve ``(session_id, criada)``. O segundo item diz se a sessao
        anterior sumiu (ou nunca existiu), para que o chamador possa reinjetar
        o historico local no proximo turno.

        A politica do bot e aplicada **sempre** que a conversa pertence a um
        bot com restricao (inclusive na sessao retomada): sem isso, uma
        conversa da Donna aberta antes do restart voltaria com todas as
        ferramentas. Conversa de bot sem restricao (o `gaia`) continua com o
        catalogo inteiro do processo.
        """
        conv = self.store.get_conversation(conversation_id)
        dono = bot or self._bot_for_mode(str(conv["mode"]))
        esperado = self._bot_directory(dono)
        sid = conv["agent_session_id"]
        if self.cfg.agent.resume_session and sid:
            existing = await self.client.get_session(sid)
            if existing and self._session_in_directory(existing, esperado):
                policy = self._policy_for(dono)
                if policy is not None:
                    self._apply_policy(sid, policy)
                return sid, False
            if existing:
                self.logger.warning(
                    "sessao %s da conversa %s pertence a %s, nao a %s; abrindo nova",
                    sid,
                    conversation_id,
                    existing.directory or "?",
                    esperado,
                )
                self.ui.warning(
                    "a conversa apontava para uma sessão de outro diretório; abrindo nova"
                )
            else:
                self.logger.warning(
                    "sessao %s da conversa %s nao existe mais; abrindo nova",
                    sid,
                    conversation_id,
                )
        session = await self.client.create_session(
            title=f"Gaia — {Path(esperado).name} · {persona.name}",
            model=self._model_for(dono),
        )
        self.store.set_session(conversation_id, session.id)
        policy = self._policy_for(dono)
        if policy is not None:
            self._apply_policy(session.id, policy)
        return session.id, True

    def _apply_policy(self, session_id: str, policy: SessionPolicy) -> None:
        """Fixa tools/diretorio/escopo na sessao (idempotente; no boot nao explode)."""
        aplicar = getattr(self.client, "set_session_policy", None)
        if not callable(aplicar):
            return
        try:
            aplicar(session_id, policy)
        except AgentError as exc:  # pragma: no cover - sessao sumiu no meio
            self.logger.warning(
                "nao foi possivel aplicar a politica do bot em %s: %s", session_id, exc
            )

    def _bot_for_mode(self, mode: str) -> Bot | None:
        """Bot dono de uma conversa (pelo id/apelido gravado em `mode`)."""
        persona = get_persona(mode)
        if persona is None:
            return None
        registry = getattr(self, "bots", None) or {}
        return registry.get(persona.key)

    def _bot_directory(self, bot: Bot | None) -> Path:
        """Diretorio da sessao: workspace do bot (relativo a raiz do projeto)."""
        if bot is None or not bot.workspace.strip():
            return self.directory
        try:
            return Path(bot.workspace).expanduser().resolve()
        except OSError:  # pragma: no cover - defensivo
            return self.directory

    def _model_for(self, bot: Bot | None):
        """Modelo do bot (`provider/model`); vazio herda o global."""
        slug = (bot.model if bot is not None else "").strip()
        if not slug:
            return self.client.model
        from gaia.agent.types import ModelRef

        return ModelRef.parse(slug)

    def _policy_for(self, bot: Bot | None) -> SessionPolicy | None:
        """Politica da sessao do bot (``None`` = sem restricao, o `gaia`).

        A Gaia nao tem allowlist nem escopo, mas continua com um `bot` na
        politica: e a origem de uma chamada `falar_com_bot` e o campo fica
        persistido na sessao como uma identidade, nao uma restricao.
        """
        if bot is None:
            return None
        if bot.tools is None and not bot.scope.strip() and bot.id == "gaia":
            return SessionPolicy(bot=bot.id)
        return SessionPolicy(
            tools=bot.tools,
            directory=str(self._bot_directory(bot)),
            scope=str(Path(bot.scope).expanduser().resolve()) if bot.scope.strip() else "",
            bot=bot.id,
        )

    def _session_in_directory(
        self, session: SessionInfo, esperado: Path | None = None
    ) -> bool:
        """A API retoma sessao de qualquer diretorio; prompt so funciona no dono."""
        if not session.directory:
            return True
        alvo = esperado or self.directory
        try:
            return Path(session.directory).resolve() == alvo.resolve()
        except OSError:
            return False

    async def _ensure_session(self) -> str:
        """Mantido para compatibilidade; le o estado atual."""
        assert self.conversation_id is not None
        sid, _created = await self._open_session(self.conversation_id, self.persona)
        return sid

    async def _activate_conversation(
        self,
        conversation_id: int,
        persona: Persona,
        *,
        seed_history: bool = True,
        bot: Bot | None = None,
    ) -> None:
        """Troca conversa+persona+sessao como uma unica operacao.

        Tudo o que depende de await acontece antes; o estado compartilhado so e
        alterado no fim, já com a sessao certa. Assim duas trocas simultaneas
        nao se cruzam.
        """
        async with self._transition_lock:
            dono = bot or self._bot_for_mode(persona.key)
            sid, created = await self._open_session(conversation_id, persona, dono)
            if created and seed_history:
                context = self._history_context(conversation_id)
                if context:
                    self._resume_context = context
                    self.logger.info(
                        "historico da conversa %s sera reinjetado no proximo turno",
                        conversation_id,
                    )
            self.persona = persona
            self.conversation_id = conversation_id
            self.session_id = sid
            self._last_assistant_message_id = None
            self.store.set_meta(self._mode_meta_key(), persona.key)
            await self._clear_stale_session(sid)

    def _history_context(self, conversation_id: int, limit: int = 12) -> str:
        """Resume os ultimos turnos locais para reconstruir o contexto."""
        lines: list[str] = []
        for row in self.store.turns(conversation_id, limit=limit):
            role = str(row["role"])
            if role not in {"user", "assistant"}:
                continue
            text = " ".join(str(row["text"]).split())
            if not text:
                continue
            if len(text) > 400:
                text = text[:400].rstrip() + "…"
            lines.append(f"{'Talita' if role == 'user' else 'Gaia'}: {text}")
        if not lines:
            return ""
        body = "\n".join(lines)
        if len(body) > 3000:
            body = body[-3000:].split("\n", 1)[-1]
        return (
            "[Contexto de continuidade — histórico local recuperado porque a "
            "sessão anterior não estava disponível. Use apenas quando for "
            "relevante.]\n" + body
        )

    def _take_resume_context(self) -> str:
        context, self._resume_context = self._resume_context, ""
        return context

    async def _clear_stale_session(self, session_id: str | None = None) -> None:
        """Aborta uma execucao presa (sessao em busy) antes de iniciar outra."""
        sid = session_id or self.session_id
        if not sid:
            return
        try:
            status = await self.client.session_status(sid)
        except AgentError:
            return
        if status and isinstance(status, dict) and status.get("type") != "idle":
            self.logger.warning("sessao presa (%s); abortando antes do proximo turno", status)
            self.ui.warning("liberando uma execução anterior presa…")
            await self.client.abort(sid)
            await asyncio.sleep(0.2)

    async def _seed_last_message_id(self) -> None:
        """Cacheia o id da ultima resposta para evitar um GET a cada turno."""
        try:
            message = await self.client.latest_assistant(self.session_id)
        except AgentError:
            return
        if message and message.id:
            self._last_assistant_message_id = message.id

    async def _loop(self) -> None:
        while True:
            if self._voice_mode and self._voice_available():
                quit_app = await self._continuous_loop()
                if quit_app:
                    return
                continue

            try:
                text = await self._read_user_input()
            except EOFError:
                self.ui.info("até logo.")
                return
            except KeyboardInterrupt:
                self.ui.info("")
                continue

            if text is None:
                continue
            self._mark_activity()
            text = text.strip()
            if not text:
                if self._voice_available():
                    self._voice_mode = True
                    self.cfg.audio.continuous = True
                continue

            self._voice_mode = False
            self.cfg.audio.continuous = False

            width = max(20, self.ui.console.width)
            lines = max(1, (len("Talita: ") + len(text) + width - 1) // width)
            self.ui.erase_input_line(lines)
            if text.startswith("/"):
                if await self._handle_command(text):
                    return
                continue

            self.ui.print_user(text)
            assert self.conversation_id is not None
            self.store.add_turn(self.conversation_id, "user", text)

            try:
                await self._dispatch(text)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.tts.stop()
                self.ui.warning("interrompido")
                continue

    def modes(self) -> list[Persona]:
        """Personas do registry efetivo (nome/diretriz do TOML valem no prompt)."""
        return [persona_from_bot(bot) for bot in self.bots_list()]

    def bots_list(self) -> list[Bot]:
        """Bots do registry na ordem do codigo (a tela monta o rail daqui)."""
        return list(getattr(self, "bots", {}).values())

    def bot_for(self, key: str | None) -> Bot | None:
        """Bot pelo id/apelido, ou ``None`` (quem chama decide o 400/aviso)."""
        persona = get_persona(key)
        if persona is None:
            return None
        return (getattr(self, "bots", None) or {}).get(persona.key)

    def bot_status(self, bot: Bot) -> tuple[bool, str]:
        """``(disponivel, motivo)``: bot configurado pode rodar nesta maquina?"""
        problema = bot_problem(bot, getattr(self, "bots", None))
        return (not problema, problema)

    def bot_unavailable_message(self, bot: Bot, motivo: str) -> str:
        """Mensagem legivel para o bot que nao pode subir (passo operacional)."""
        return (
            f"o bot {bot.name} ({bot.id}) nao esta disponivel: {motivo}. "
            "Ajuste [bots.{}] no config ou crie a pasta.".format(bot.id)
        )

    @property
    def mode_key(self) -> str:
        return self.persona.key

    @property
    def mode_name(self) -> str:
        return self.persona.name

    def sessions(self) -> list[dict[str, Any]]:
        """Conversas do bot ativo, incluindo as gravadas com a chave antiga.

        A fase 1 renomeou os modos (`pesquisadora` -> `donna`), mas o `mode`
        das conversas antigas nao muda sozinho: sem ler as chaves de
        compatibilidade, o historico anterior sumiria da tela.
        """
        vistos: dict[int, dict[str, Any]] = {}
        for chave in stored_mode_keys(self.persona.key, getattr(self, "bots", None)):
            for row in self.store.list_conversations(
                str(self.directory), chave, self.channel
            ):
                identificador = int(row["id"])
                if identificador in vistos:
                    continue
                vistos[identificador] = {
                    "id": identificador,
                    "title": row["title"] or "Sem título",
                    "updated_at": row["updated_at"],
                    "turns": int(row["turn_count"]),
                    "session_id": row["agent_session_id"] or "",
                    "current": identificador == self.conversation_id,
                }
        return sorted(
            vistos.values(), key=lambda item: (item["updated_at"], item["id"]),
            reverse=True,
        )

    def history(self) -> list[tuple[str, str]]:
        if self.conversation_id is None:
            return []
        return [
            (row["role"], row["text"])
            for row in self.store.turns(self.conversation_id, limit=200)
        ]

    async def select_mode(self, key: str) -> None:
        """Troca de persona (compatibilidade): e a mesma coisa que `select_bot`."""
        persona = get_persona(key)
        if persona is None:
            return
        await self.select_bot(persona.key)

    async def select_bot(self, key: str) -> Bot:
        """Ativa um bot: conversa propria, sessao propria e politica da sessao.

        Levanta ``ValueError`` com id desconhecido (nunca cai no padrao em
        silencio) e ``BotIndisponivel`` quando o bot esta bem configurado mas
        depende de algo que esta maquina ainda nao tem (ex.: o clone da
        dissertacao). A politica (tools/workspace/escopo) e aplicada em
        `_open_session`, antes de qualquer turno.
        """
        bot = require_bot(key, getattr(self, "bots", None))
        disponivel, motivo = self.bot_status(bot)
        if not disponivel:
            raise BotIndisponivel(self.bot_unavailable_message(bot, motivo))
        persona = persona_from_bot(bot)
        conv = self.store.get_or_create_conversation(
            str(self.directory), bot.id, self.channel
        )
        await self._activate_conversation(int(conv["id"]), persona, bot=bot)
        return bot

    async def select_conversation(self, conversation_id: int) -> None:
        try:
            conv = self.store.get_conversation(conversation_id)
        except KeyError:
            return
        persona = self._persona_for_mode(str(conv["mode"]))
        self.store.set_current(conversation_id)
        await self._activate_conversation(conversation_id, persona)

    def _persona_for_mode(self, mode: str) -> Persona:
        """Persona de uma conversa gravada (chave antiga ou id de bot)."""
        bot = self._bot_for_mode(mode)
        if bot is not None:
            return persona_from_bot(bot)
        return resolve_mode(mode)

    async def new_session(self) -> None:
        await self._new_session()

    def mark_session_dirty(self) -> None:
        """Pede uma checagem de sessão presa no próximo turno (ex.: após abortar)."""
        self._session_dirty = True
