"""Comandos de barra (/status, /nota, /foco, /carteira, ...)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from gaia.agenda.client import AgendaError
from gaia.agent.providers.factory import normalize_provider
from gaia.bots import BotIndisponivel
from gaia.config import ECHO_LANGUAGES, ECHO_NAMES
from gaia.controller.contract import OrchestratorContract
from gaia.controller.modes import get_persona
from gaia.controller.proactive import parse_reminder
from gaia.finance.quotes import FinanceError, fetch_quote, format_quote
from gaia.focus import moon_details, period_label, principal_phase_today
from gaia.tasks.store import Task


def escape_user(text: str) -> str:
    from rich.markup import escape

    return escape(str(text))


# Instrucao de traducao, system prompt e titulo da sessao por idioma do eco.
# O chines mantem o formato antigo (``中文 | pinyin``); ingles e frances saem
# so a traducao, que e o que cabe embaixo da bolha no web.
ECHO_PROMPTS: dict[str, tuple[str, str, str]] = {
    "zh": (
        "Traduza o texto abaixo para chinês simplificado e escreva também "
        "o pinyin. Responda APENAS no formato: 中文 | pinyin",
        "Você é um tradutor português-chinês. Seja fiel e conciso.",
        "Gaia — 中文",
    ),
    "en": (
        "Translate the text below into natural English. Reply with ONLY the "
        "translation, no quotes and no notes.",
        "You are a Portuguese-to-English translator. Stay faithful and concise.",
        "Gaia — English",
    ),
    "fr": (
        "Traduisez le texte ci-dessous en français naturel. Répondez UNIQUEMENT "
        "avec la traduction, sans guillemets ni commentaires.",
        "Vous êtes un traducteur portugais-français. Soyez fidèle et concis.",
        "Gaia — Français",
    ),
}


class CommandsMixin(OrchestratorContract):
    async def _handle_command(self, text: str) -> bool:
        command, _, argument = text.partition(" ")
        command = command.lower().strip()
        argument = argument.strip()

        if command in {"/sair", "/quit", "/exit"}:
            self.ui.info("até logo.")
            return True
        if command in {"/reiniciar", "/restart"}:
            self._restart_requested = True
            self.ui.info("reiniciando…")
            return True
        if command in {"/", "/ajuda", "/help"}:
            self.ui.help()
        elif command == "/status":
            await self._show_status()
        elif command in {"/tokens", "/consumo"}:
            await self._show_tokens()
        elif command in {"/novo", "/nova"}:
            await self._new_session()
        elif command == "/sessao":
            self.ui.info(f"sessão do agente: {self.session_id}")
        elif command == "/buscar":
            if not argument:
                self.ui.warning("uso: /buscar <termo>")
            else:
                hits = await asyncio.to_thread(
                    self.index.search, argument, max(5, self.cfg.secondbrain.context_results)
                )
                self.ui.search_results(hits)
        elif command == "/nota":
            if not argument:
                self.ui.warning("uso: /nota <título>")
            else:
                try:
                    note = await asyncio.to_thread(self.brain.read_note, argument)
                    self.ui.print_note(note)
                except FileNotFoundError as exc:
                    self.ui.warning(str(exc))
        elif command == "/indexar":
            self.ui.info("reindexando Second Brain...")
            try:
                stats = await asyncio.to_thread(self.index.reindex, True)
                self.ui.success(
                    f"{stats['indexed']} nota(s) indexada(s), {stats['total']} no total"
                )
            except Exception as exc:
                self.ui.error(f"falha ao indexar: {exc}")
        elif command in {"/modelo", "/modelos", "/model"}:
            await self._switch_model(argument)
        elif command in {"/bot", "/bots"}:
            await self._switch_bot(argument)
        elif command in {"/modo", "/perfil", "/persona"}:
            await self._switch_mode(argument)
        elif command in {"/metas", "/objetivos", "/painel"}:
            await self._show_dashboard()
        elif command in {"/bolsa", "/acao", "/ações"}:
            await self._handle_quote(argument)
        elif command in {"/carteira", "/watchlist", "/watch"}:
            await self._handle_watchlist(argument)
        elif command in {"/foco", "/focos"}:
            await self._handle_focus(argument)
        elif command in {"/tarefas", "/tarefa"}:
            await self._handle_tasks(argument)
        elif command == "/backlog":
            await self._handle_backlog(argument)
        elif command == "/voz":
            self._switch_voice(argument)
        elif command in {"/audiobook", "/audiolivro"}:
            await self._handle_audiobook(argument)
        elif command in {"/audiobooks", "/audiolivros"}:
            await self._handle_audiobooks_inbox()
        elif command == "/interromper":
            await self.client.abort(self.session_id)
            self.tts.stop()
            self.ui.info("interrupção enviada.")
        elif command in {"/lembrar", "/lembrete"}:
            if not argument:
                self.ui.warning("uso: /lembrar <texto> em 30 minutos")
            else:
                spec = parse_reminder(argument, require_prefix=False)
                if spec is None:
                    self.ui.warning("não entendi o horário. Ex.: /lembrar beber água em 30 minutos")
                else:
                    await self._handle_reminder(spec)
        elif command == "/lembretes":
            self.ui.reminders(self.store.pending_reminders())
        elif command == "/cancelar":
            try:
                reminder_id = int(argument)
            except ValueError:
                self.ui.warning("uso: /cancelar <id>")
            else:
                if self.store.cancel_reminder(reminder_id):
                    self.ui.success(f"lembrete #{reminder_id} cancelado")
                else:
                    self.ui.warning(f"lembrete #{reminder_id} não encontrado")
        elif command == "/proativo":
            self._toggle_proactive(argument)
        elif command in {"/continuo", "/contínuo"}:
            self._toggle_continuous(argument)
        elif command in {"/fala", "/ouvir"}:
            self._toggle_continuous("on")
        elif command in {"/texto", "/digitar"}:
            self._toggle_continuous("off")
        elif command == "/contexto":
            self._toggle_context(argument)
        elif command in {"/rapido", "/rápido", "/fast"}:
            self._toggle_fast(argument)
        elif command in {"/chines", "/chinês", "/中文"}:
            self._toggle_chinese(argument)
        elif command in {"/pensando", "/pensar", "/thinking", "/raciocinio", "/raciocínio"}:
            self._toggle_reasoning(argument)
        elif command in {"/consolidar", "/memoria", "/memória"}:
            await self._handle_consolidate(argument)
        elif command in {"/sonhar", "/sonho"}:
            await self._handle_dream(argument)
        elif command in {"/preferencia", "/preferência"}:
            if not argument:
                self.ui.warning("uso: /preferencia <texto>")
            else:
                await self._handle_preference(argument)
        elif command in {"/preferencias", "/preferências"}:
            text = self.brain.load_preferences()
            self.ui.print_markdown(text or "(sem preferências registradas)", "Preferências")
        elif command == "/confirmar":
            self._toggle_create_confirmation(argument)
        elif command in {"/autoaprovar", "/autoapprove"}:
            self._toggle_auto_approve(argument)
        elif command == "/historico":
            self._show_history()
        else:
            self.ui.warning(f"comando desconhecido: {command} (use /ajuda)")
        return False

    async def _show_status(self) -> None:
        health = await self.client.health()
        sb_stats = self.index.stats()
        mem = self.store.stats()
        self.ui.status(
            self.directory,
            self.session_id,
            self.cfg.agent.model_slug(),
            sb_stats["notes"],
            self.tts.provider,
        )
        self.ui.info(f"provedor: {self.client.endpoint} · saudável={bool(health)}")
        self.ui.info(
            f"memória operacional: {mem['conversations']} conversa(s), "
            f"{mem['turns']} turno(s), {mem['records']} registro(s)"
        )
        pending = self.store.reminder_stats()["pending"]
        self.ui.info(
            f"proativo: {'ativo' if self.cfg.proactive.enabled else 'desativado'} · "
            f"{pending} lembrete(s) pendente(s)"
        )
        self.ui.info(f"second brain: {self.brain.root} · {sb_stats}")
        self.ui.info(f"modo ativo: {self.persona.name} ({self.persona.key})")
        fast_model = self.cfg.agent.fast_model or "desligado"
        self.ui.info(
            "economia: "
            f"contexto={self.cfg.secondbrain.context_results}x"
            f"{self.cfg.secondbrain.context_chars} chars · "
            f"histórico≤{self.cfg.agent.max_history_messages} msgs/"
            f"{self.cfg.agent.max_history_chars} chars · "
            f"ferramentas={'todas' if self.cfg.agent.tools else 'desligadas'} · "
            f"resultado≤{self.cfg.agent.tool_result_chars} chars · "
            f"resumo={self.cfg.agent.history_summary} · "
            f"cache={'on' if self.cfg.agent.cache_prompt else 'off'} · "
            f"modelo rápido={fast_model}"
        )
        usage_fn = getattr(self.client, "usage", None)
        usage = usage_fn(self.session_id) if callable(usage_fn) and self.session_id else {}
        last = usage.get("last") or {}
        total = usage.get("total") or {}
        if last or total:
            last_in = int(last.get("input_tokens", 0))
            last_cached = int(last.get("cached_tokens", 0))
            share = f", {round(100 * last_cached / last_in)}% do input" if last_in else ""
            self.ui.info(
                f"tokens: último turno {last_in} in "
                f"({last_cached} em cache{share}) / "
                f"{last.get('output_tokens', 0)} out · sessão "
                f"{total.get('input_tokens', 0)} in / "
                f"{total.get('output_tokens', 0)} out"
            )
        else:
            self.ui.info("tokens: sem medição neste provedor/turno")

    async def _show_tokens(self) -> None:
        usage_fn = getattr(self.client, "usage", None)
        usage = (
            usage_fn(self.session_id)
            if callable(usage_fn) and self.session_id
            else {}
        )
        history = usage.get("history") or []
        total = usage.get("total") or {}
        if not history and not total:
            self.ui.info("sem medição de tokens nesta sessão/provedor")
            return
        self.ui.info(f"tokens da sessão {self.session_id} (últimos turnos):")
        start = max(1, len(history) - 9)
        for number, item in enumerate(history[-10:], start=start):
            cached = int(item.get("cached_tokens", 0))
            self.ui.info(
                f"  #{number}: {item.get('input_tokens', 0)} in "
                f"({cached} em cache) / {item.get('output_tokens', 0)} out"
            )
        self.ui.info(
            f"total: {total.get('input_tokens', 0)} in / "
            f"{total.get('output_tokens', 0)} out · "
            f"{total.get('cached_tokens', 0)} vieram de cache"
        )

    async def _new_session(self) -> None:
        """Nova conversa do bot ativo, com a politica da sessao dele."""
        async with self._transition_lock:
            bot = self._bot_for_mode(self.persona.key)
            workspace = self._bot_directory(bot)
            conv = self.store.create_conversation(
                str(self.directory),
                title="Nova sessão",
                mode=self.persona.key,
                channel=self.channel,
            )
            conversation_id = int(conv["id"])
            session = await self.client.create_session(
                title=f"Gaia — {workspace.name} · {self.persona.name}",
                model=self._model_for(bot),
            )
            self.store.set_session(conversation_id, session.id)
            policy = self._policy_for(bot)
            if policy is not None:
                self._apply_policy(session.id, policy)
            self.conversation_id = conversation_id
            self.session_id = session.id
            self._last_assistant_message_id = None
            self._resume_context = ""
            self.store.set_meta(self._mode_meta_key(), self.persona.key)
        self.ui.success(f"nova sessão: {session.id}")

    async def _switch_model(self, argument: str) -> None:
        for kind, text in await self.model_messages(argument):
            getattr(self.ui, kind)(text)

    async def model_messages(self, argument: str) -> list[tuple[str, str]]:
        """Resposta do /modelo em pares (nível, texto).

        O terminal imprime cada par com ``ui.info``/``success``/``warning``; o
        Telegram junta o texto, porque a UI de lá é ``quiet``.
        """
        from gaia.agent.providers import SHORTCUTS, resolve_shortcut
        from gaia.agent.types import ModelRef

        value = (argument or "").strip()
        if not value:
            messages, _options = await self.show_model_options()
            return messages
        # Atalho (cc, ds, glm, turbo, zen): vem de tabela, sem consultar provedor.
        shortcut = resolve_shortcut(value, self._last_models())
        if shortcut is not None:
            provider, model = shortcut
            return await self._model_switch_messages(ModelRef(provider, model))
        if value.lower() in SHORTCUTS:
            # Atalho conhecido sem destino: o provedor nao tem padrao nem uso
            # anterior. Dizer isso e melhor do que tentar ``zen`` como modelo.
            return [
                (
                    "warning",
                    f"atalho '{value}' sem modelo definido; "
                    "use /modelo provider/model uma vez para memorizar",
                )
            ]
        if value.isdigit():
            messages, options = await self.show_model_options()
            index = int(value) - 1
            if not 0 <= index < len(options):
                return [("warning", f"número fora da lista (1-{len(options)})")]
            return await self._model_switch_messages(options[index].as_ref())
        if "/" in value:
            try:
                reference = ModelRef.parse(value)
            except ValueError as exc:
                return [("error", str(exc))]
            return await self._model_switch_messages(reference)
        # Sem barra: id do modelo. A lista ao vivo decide o provedor quando o
        # id existe em apenas um deles; senão vale o provedor atual.
        _messages, options = await self.show_model_options()
        matches = [item for item in options if item.model == value]
        if len(matches) == 1:
            reference = matches[0].as_ref()
        else:
            reference = ModelRef(
                provider_id=normalize_provider(self.cfg.agent.provider),
                model_id=value,
            )
        return await self._model_switch_messages(reference)

    def _last_models(self) -> dict[str, str]:
        """Ultimo modelo usado em cada provedor (memoria por canal, sem rede)."""
        from gaia.agent.providers import PRESETS

        current = normalize_provider(self.cfg.agent.provider)
        last = {current: self.cfg.agent.model}
        for provider in PRESETS:
            remembered = self.store.get_meta(f"model:{provider}")
            if remembered:
                last.setdefault(provider, remembered)
        return last

    async def show_model_options(self) -> tuple[list[tuple[str, str]], list]:
        """Lista numerada dos modelos e as opções cruas (para o seletor)."""
        from gaia.agent.catalog import MAX_CHOICES, collect_models, render_choices
        from gaia.agent.providers import SHORTCUTS

        catalog = await collect_models(self.cfg, client=self.client)
        messages: list[tuple[str, str]] = []
        messages.append(
            (
                "info",
                "atalhos: "
                + " · ".join(f"/modelo {nome}" for nome in SHORTCUTS)
                + " (sem consultar a rede)",
            )
        )
        if catalog.options:
            messages.extend(("info", line) for line in render_choices(catalog.options))
            if len(catalog.options) > MAX_CHOICES:
                messages.append(
                    (
                        "info",
                        f"… e mais {len(catalog.options) - MAX_CHOICES}; "
                        "use o slug completo para escolher qualquer um",
                    )
                )
        messages.extend(("warning", f"{name}: {error}") for name, error in catalog.errors)
        if not catalog.options:
            messages.append(
                ("warning", "nenhum modelo listado; use /modelo provider/model")
            )
        current = catalog.options[0].slug if catalog.options else self.cfg.agent.model_slug()
        messages.append(
            (
                "info",
                f"use /modelo <número> ou /modelo provider/model · atual: {current}",
            )
        )
        return messages, catalog.options

    async def _model_switch_messages(self, reference) -> list[tuple[str, str]]:
        provider = normalize_provider(reference.provider_id)
        slug = f"{provider}/{reference.model_id}"
        previous = f"{normalize_provider(self.cfg.agent.provider)}/{self.cfg.agent.model}"
        previous_fast = self.cfg.agent.fast_model
        self.cfg.agent.apply_model(provider, reference.model_id)
        # O modelo rapido pertence ao provedor anterior: sem isso o id da
        # Command Code iria no request da DeepSeek (400 em pedido curto).
        self.cfg.agent.fast_model = self._fast_model_for(provider)
        self.store.set_meta("model", slug)
        self.store.set_meta(f"model:{provider}", reference.model_id)
        messages: list[tuple[str, str]] = [
            ("success", f"modelo da próxima mensagem: {slug}")
        ]
        if previous == slug and previous_fast == self.cfg.agent.fast_model:
            return messages
        # O AgentClient guarda o provedor pronto; sem descartar, endpoint e
        # chave antigos continuariam valendo ate reiniciar.
        reset = getattr(self.client, "reset_provider", None)
        if callable(reset):
            await reset()
        messages.extend(
            self._save_model_choice(
                provider, reference.model_id, fast_model=self.cfg.agent.fast_model
            )
        )
        return messages

    @staticmethod
    def _fast_model_for(provider: str) -> str:
        """Modelo rapido declarado no preset do provedor (vazio = nao roteia)."""
        from gaia.agent.providers import PRESETS

        preset = PRESETS.get(provider)
        return preset.fast_model if preset else ""


    def _save_model_choice(
        self, provider: str, model: str, *, fast_model: str | None = None
    ) -> list[tuple[str, str]]:
        """Persiste a escolha no config.toml (nunca segredo, nunca meta)."""
        from gaia.config import model_env_override, save_agent_model

        messages: list[tuple[str, str]] = []
        config_file = Path(self.cfg.config_file).expanduser() if self.cfg.config_file else None
        try:
            path = save_agent_model(provider, model, config_file, fast_model=fast_model)
        except (OSError, ValueError) as exc:
            messages.append(("warning", f"não consegui salvar em config.toml: {exc}"))
            return messages
        messages.append(("info", f"salvo em {path}"))
        overrides = model_env_override()
        if overrides:
            messages.append(
                (
                    "warning",
                    "atenção: "
                    + ", ".join(overrides)
                    + " no ambiente/.env sobrescreve o config ao iniciar",
                )
            )
        return messages

    async def _show_dashboard(self) -> None:
        moon = moon_details()
        note = ""
        phase = principal_phase_today(datetime.now().astimezone() + timedelta(days=1))
        if phase:
            _key, name, moment = phase
            try:
                manifestation = await self.agenda.lunation_note(
                    moment.date().isoformat()
                )
            except AgendaError:
                manifestation = ""
            note = f"Amanhã: {name}"
            if manifestation:
                note += f" — manifestações na Agenda: {manifestation}"
        self.ui.dashboard(self.focus.current, self.goals.all(), moon, note)

    async def _handle_quote(self, argument: str) -> None:
        ticker = argument.strip().split()[0] if argument.strip() else ""
        if not ticker:
            self.ui.warning("uso: /bolsa <ticker> (ex.: PETR4, AAPL)")
            return
        try:
            quote = await fetch_quote(ticker)
        except FinanceError as exc:
            self.ui.error(str(exc))
            return
        self.ui.info(format_quote(quote))

    async def _handle_watchlist(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub in {"add", "adicionar", "+"}:
            if not rest:
                self.ui.warning("uso: /carteira add <ticker> ou /carteira add Nome=TICKER")
                return
            if "=" in rest:
                label, ticker = rest.split("=", 1)
            else:
                label, ticker = rest, rest
            item = self.watchlist.add(label, ticker)
            self.ui.success(f"{item.label} ({item.ticker}) na carteira")
            return
        if sub in {"remove", "remover", "del", "-"}:
            if not rest:
                self.ui.warning("uso: /carteira remover <ticker>")
                return
            if self.watchlist.remove(rest):
                self.ui.success(f"{rest.upper()} removido da carteira")
            else:
                self.ui.warning(f"{rest.upper()} não estava na carteira")
            return
        items = self.watchlist.all()
        if not items:
            self.ui.info("carteira vazia; use /carteira add <ticker>")
            return
        results = await asyncio.gather(
            *(fetch_quote(item.ticker) for item in items), return_exceptions=True
        )
        rows: list[dict[str, str]] = []
        for item, result in zip(items, results, strict=False):
            if isinstance(result, BaseException):
                rows.append(
                    {
                        "label": item.label,
                        "ticker": item.ticker,
                        "price": "?",
                        "change": "?",
                    }
                )
                continue
            price = f"{result.price:.2f}" if result.price is not None else "?"
            change = (
                f"{result.change_pct:+.2f}%"
                if result.change_pct is not None
                else "?"
            )
            rows.append(
                {
                    "label": item.label,
                    "ticker": result.symbol,
                    "price": f"{result.currency} {price}".strip(),
                    "change": change,
                }
            )
        self.ui.watchlist(rows)

    async def _handle_focus(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=2)
        sub = parts[0].lower() if parts else ""
        if sub in {"definir", "novo", "set"}:
            if len(parts) < 3:
                self.ui.warning("uso: /foco definir semanal|mensal|trimestral <texto>")
                return
            period_raw = parts[1].lower()
            period = {
                "semanal": "week",
                "semana": "week",
                "mensal": "month",
                "mes": "month",
                "mês": "month",
                "trimestral": "quarter",
                "trimestre": "quarter",
            }.get(period_raw)
            if period is None:
                self.ui.warning("período inválido; use semanal, mensal ou trimestral")
                return
            text = parts[2].strip()
            goals = [part.strip() for part in re.split(r"[;,]", text) if part.strip()]
            if len(goals) <= 1:
                goals = []
            focus = self.focus.set(period, text, goals)
            self.ui.success(f"foco {period_label(focus.period)} definido até {focus.end}")
            await self._show_dashboard()
            return
        if sub in {"limpar", "clear", "remover"}:
            self.focus.clear()
            self.ui.success("foco limpo")
            return
        current = self.focus.current
        if current is None:
            self.ui.info("nenhum foco definido; use /foco definir semanal <texto>")
        else:
            self.ui.info(
                f"foco {period_label(current.period)}: {current.label} "
                f"({current.start} → {current.end})"
            )
            for goal in current.goals:
                self.ui.info(f"  • {goal}")
        detail = moon_details()
        self.ui.info(
            f"{detail.moon_emoji} {detail.phase_name} · "
            f"{detail.sign_symbol} {detail.sign_name} · "
            f"{detail.element_emoji} {detail.element} · {detail.phase_emoji}"
        )

    def _find_task(self, query: str) -> Task | None:
        query = query.strip().lower()
        if not query:
            return None
        for task in self.tasks.all():
            if task.id.startswith(query) or query in task.title.lower():
                return task
        return None

    async def _handle_tasks(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in {"limpar", "clean", "limpar-concluidas"}:
            count = self.tasks.clear_done()
            self.ui.success(f"{count} tarefa(s) concluída(s) removida(s)")
            return
        if sub in {"sync", "sincronizar", "atualizar"}:
            try:
                pages = await self.agenda.pages(30)
            except AgendaError as exc:
                self.ui.error(str(exc))
                return
            page = next(
                (
                    item
                    for item in pages
                    if "tarefa" in str(item.get("title") or "").lower()
                ),
                None,
            )
            if page is None:
                self.ui.warning("não achei uma página de tarefas na Agenda")
                return
            synced = self.tasks.sync_text(str(page.get("content") or ""))
            self.ui.success(
                f"{len(synced)} tarefa(s) sincronizada(s) de '{page.get('title')}'"
            )
        elif sub in {"fazer", "executar", "run"}:
            task = self._find_task(rest)
            if task is None:
                self.ui.warning("uso: /tarefas fazer <id ou trecho do título>")
                return
            self.tasks.set_status(task.id, "in_progress")
            prompt = (
                f"Tarefa de longo prazo registrada na Agenda: {task.title}\n\n"
                "Execute isso no repositório atual, com mudanças mínimas e testes. "
                "Ao terminar, explique o que fez e o que ainda falta."
            )
            await self._handle_task_impl(prompt, speak=True)
            return
        elif sub in {"concluir", "done", "feito"}:
            task = self._find_task(rest)
            if task is None:
                self.ui.warning("uso: /tarefas concluir <id ou trecho do título>")
                return
            self.tasks.set_status(task.id, "done")
            self.ui.success(f"tarefa concluída: {task.title}")
            return

        tasks = self.tasks.all()
        if not tasks:
            self.ui.info("nenhuma tarefa registrada; use /tarefas sync")
            return
        symbols = {"done": "✓", "in_progress": "◐", "pending": "•"}
        for task in tasks:
            symbol = symbols.get(task.status, "•")
            self.ui.info(f"{symbol} [{task.status}] {task.id} — {task.title}")
            if task.notes:
                self.ui.info(f"   {task.notes}")

    async def _switch_mode(self, argument: str) -> None:
        """`/modo`: alias antigo de `/bot` (mesmos bots, mesmos apelidos)."""
        await self._switch_bot(argument)

    async def _switch_bot(self, argument: str) -> None:
        """`/bot [nome]`: lista sem argumento; troca com o nome ou um apelido."""
        if not argument:
            self.ui.info(f"bot atual: {self.persona.name} ({self.persona.key})")
            for item in self.modes():
                self.ui.info(f"  {item.key}: {item.name} — {item.summary}")
            return
        persona = get_persona(argument)
        if persona is None:
            options = ", ".join(persona.key for persona in self.modes())
            self.ui.warning(f"bot desconhecido: {argument} (opções: {options})")
            return
        try:
            bot = await self.select_bot(persona.key)
        except BotIndisponivel as exc:
            # Configuracao certa, ambiente faltando (ex.: clone da dissertacao):
            # aviso claro em vez de troca silenciosa para o bot errado.
            self.ui.warning(str(exc))
            return
        self.ui.success(f"bot ativo: {bot.name} — {bot.role}")

    async def _handle_audiobook(self, argument: str) -> None:
        """`/audiobook <arquivo> [titulo]`: narra um PDF/TXT da pasta de entrada."""
        cfg = self.cfg.audiobook
        if not cfg.enabled:
            self.ui.warning("audiobook desligado (audiobook.enabled=false)")
            return
        if not argument:
            self.ui.info(
                f"pasta de entrada: {self.cfg.audiobook_inbox()}\n"
                f"saída: {self.cfg.audiobook_outbox()}\n"
                "uso: /audiobook <arquivo.pdf> [título] · /audiobooks gera a pasta inteira"
            )
            return
        name, _, title = argument.partition(" ")
        inbox = self.cfg.audiobook_inbox()
        source = Path(name).expanduser()
        if not source.is_file():
            source = inbox / name
        if not source.is_file():
            self.ui.warning(f"não achei {name} (procurei também em {inbox})")
            return
        from gaia.audiobook.jobs import AudiobookError, AudiobookJob, build_audiobook

        job = AudiobookJob(
            source=source,
            outbox=self.cfg.audiobook_outbox(),
            cfg=cfg,
            tmp_dir=cfg.tmp_path(self.cfg.resolved_data_dir()),
            title=title.strip(),
        )
        self.ui.info(f"narrando {source.name} ({job.title})…")
        try:
            output = await build_audiobook(job)
        except AudiobookError as exc:
            self.ui.error(f"audiobook falhou: {exc}")
            return
        self.ui.success(f"audiobook pronto: {output}")

    async def _handle_audiobooks_inbox(self) -> None:
        from gaia.audiobook.service import process_inbox

        cfg = self.cfg.audiobook
        inbox = self.cfg.audiobook_inbox()
        self.ui.info(f"conferindo {inbox}…")
        result = await process_inbox(
            cfg,
            inbox,
            self.cfg.audiobook_outbox(),
            cfg.tmp_path(self.cfg.resolved_data_dir()),
            logging.getLogger("gaia.audiobook"),
        )
        if result.empty:
            self.ui.info("nada novo na pasta de entrada.")
            return
        for path in result.done:
            self.ui.success(str(path))
        for source, error in result.failed:
            self.ui.error(f"{source.name}: {error}")

    def _switch_voice(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "ligar"}:
            self.tts.enabled = True
            self.ui.success("voz ativada")
        elif arg in {"off", "desligar"}:
            self.tts.enabled = False
            self.ui.success("voz desativada")
        elif arg in {"edge", "say", "none"}:
            self.tts.set_provider(arg)
            self.ui.success(f"provedor de voz: {arg}")
        elif not arg:
            self.ui.info(
                f"voz={'ligada' if self.tts.enabled else 'desligada'} · provedor={self.tts.provider}"
            )
        else:
            self.ui.warning("uso: /voz on|off|edge|say|none")

    def _toggle_chinese(self, argument: str) -> None:
        arg = argument.lower().strip()
        # `/chines en|fr` troca o idioma do eco sem sair do comando (o padrao
        # continua sendo o chines).
        if arg in ECHO_LANGUAGES:
            self.cfg.interface.echo_language = arg
            self.cfg.interface.chinese_echo = True
            self.ui.success(f"eco em {self._echo_nome(arg)} ativado")
            return
        if arg in {"on", "off"}:
            self.cfg.interface.chinese_echo = arg == "on"
        else:
            self.cfg.interface.chinese_echo = not self.cfg.interface.chinese_echo
        state = "ativado" if self.cfg.interface.chinese_echo else "desativado"
        idioma = self._echo_nome(self.cfg.interface.echo_language)
        self.ui.success(f"eco em {idioma} {state}")

    @staticmethod
    def _echo_nome(chave: str) -> str:
        return ECHO_NAMES.get(str(chave or "zh").lower(), "chinês")

    @property
    def _echo_key(self) -> str:
        """Idioma do eco escolhido (zh | en | fr)."""
        chave = str(getattr(self.cfg.interface, "echo_language", "zh") or "zh").lower()
        return chave if chave in ECHO_LANGUAGES else "zh"

    async def translate_zh(self, text: str) -> str:
        """Traduz para o idioma do eco (padrao chines, com pinyin)."""
        return await self.translate_echo(text, "zh")

    async def translate_echo(self, text: str, lang: str | None = None) -> str:
        """Traduz para o idioma do eco e devolve a linha pronta.

        Chines sai ``中文 | pinyin`` (comportamento antigo); ingles e frances
        saem so a traducao. O resultado fica em cache por idioma e a falha do
        modelo (rede, timeout) devolve string vazia — o eco e opcional.
        """
        source = (text or "").strip()
        if not source:
            return ""
        codigo = str(lang or self._echo_key).lower()
        if codigo not in ECHO_LANGUAGES:
            codigo = "zh"
        cache = self._echo_cache.setdefault(codigo, {})
        cached = cache.get(source)
        if cached:
            return cached
        instrucao, system, titulo = ECHO_PROMPTS[codigo]
        prompt = f"{instrucao}\n\nTexto: {source}"
        session_id = self._echo_sessions.get(codigo)
        try:
            if not session_id:
                session = await self.client.create_session(
                    title=titulo, model=self.client.model
                )
                session_id = session.id
                self._echo_sessions[codigo] = session_id
            response = await asyncio.wait_for(
                self.client.prompt_sync(session_id, prompt, system=system),
                timeout=20.0,
            )
            output = " ".join(response.text.split()).strip()
            if output:
                cache[source] = output
                return output
        except Exception as exc:  # pragma: no cover - depende do modelo/rede
            self.logger.warning("tradução %s falhou: %s", codigo, exc)
        return ""

    def _toggle_reasoning(self, argument: str) -> None:
        """Liga/desliga o raciocinio do modelo na tela (nao muda o do Telegram)."""
        arg = argument.lower().strip()
        if arg in {"on", "ligar", "ligado"}:
            self.cfg.interface.show_reasoning = True
        elif arg in {"off", "desligar", "desligado"}:
            self.cfg.interface.show_reasoning = False
        else:
            self.cfg.interface.show_reasoning = not self.cfg.interface.show_reasoning
        state = "ativado" if self.cfg.interface.show_reasoning else "desativado"
        self.ui.success(f"raciocínio na tela {state}")
        if not self.cfg.interface.show_reasoning:
            self.ui.hint("o Telegram tem interruptor próprio: /pensando no chat")

    def _toggle_fast(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.interface.fast = arg == "on"
        else:
            self.cfg.interface.fast = not self.cfg.interface.fast
        state = "ativado" if self.cfg.interface.fast else "desativado"
        self.ui.success(f"modo rápido {state}")

    def _toggle_context(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.secondbrain.auto_context = arg == "on"
        else:
            self.cfg.secondbrain.auto_context = not self.cfg.secondbrain.auto_context
        state = "ativado" if self.cfg.secondbrain.auto_context else "desativado"
        self.ui.success(f"contexto automático do Second Brain {state}")

    def _toggle_create_confirmation(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.secondbrain.confirm_create = arg == "on"
        else:
            self.cfg.secondbrain.confirm_create = not self.cfg.secondbrain.confirm_create
        state = "pedindo confirmação" if self.cfg.secondbrain.confirm_create else "sem confirmação"
        self.ui.success(f"criação de nota: {state}")

    def _toggle_auto_approve(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.agent.auto_approve = arg == "on"
        else:
            self.cfg.agent.auto_approve = not self.cfg.agent.auto_approve
        if self.cfg.agent.auto_approve:
            self.ui.warning(
                "auto-aprovação de ferramentas ATIVA: o agente pode executar "
                "comandos e editar arquivos sem perguntar."
            )
        else:
            self.ui.success("auto-aprovação de ferramentas desativada")

    def _show_history(self) -> None:
        assert self.conversation_id is not None
        turns = self.store.turns(self.conversation_id, limit=10)
        if not turns:
            self.ui.info("sem histórico nesta conversa.")
            return
        for turn in turns:
            text = str(turn["text"])
            if len(text) > 300:
                text = text[:300] + "..."
            self.ui.console.print(f"[bold]{escape_user(turn['role'])}[/]: {escape_user(text)}")
