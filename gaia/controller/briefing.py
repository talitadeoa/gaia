"""Briefings diarios: checa a Agenda e envia no Telegram, sem LLM.

A hora nao e decidida aqui (fase C): as automacoes ``briefing:morning``,
``briefing:noon``, ``briefing:evening`` e ``market:watchlist`` vivem na tabela
``automations`` e o ``_scheduler_loop`` chama os handlers ``_automation_*``.
Cada ``_send_*`` continua com a reserva ``claim_meta`` (segunda barreira) e o
``record_run`` so registra execucao real.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from gaia.agenda.client import AgendaError
from gaia.briefing.compose import (
    collect_habits,
    collect_tasks,
    day_recap,
    evening_message,
    habits_from_routines,
    morning_message,
    noon_message,
    partition,
    weekday_label,
)
from gaia.briefing.market import MarketRow, market_message
from gaia.control.automations import Automation
from gaia.control.runs import record_run
from gaia.controller.contract import OrchestratorContract
from gaia.controller.prompt import humor_directive
from gaia.finance.quotes import fetch_quote

_PERSONAL_NOTE_PROMPT = (
    "Agora são {hora} de {weekday}, {date}. Você é a Gaia, agente pessoal da "
    "Talita, e está mandando a mensagem do dia no Telegram.\n\n"
    "O que você sabe agora:\n{context}\n\n"
    "Escreva UMA frase curta (até 30 palavras), em português do Brasil, informal "
    "e calorosa, que soe feita pra ela hoje — não um aviso genérico. Pode comentar "
    "uma tarefa, o peso do dia, uma meta ou algo que você notou nela. Não repita a "
    "lista, não use emoji, listas, título nem aspas, e não dê conselho "
    "motivacional. Responda APENAS com a frase."
)
_SLOT_LABEL = {"morning": "início da manhã", "noon": "meio do dia"}

_INSIGHT_PROMPT = (
    "Você é a Gaia, agente pessoal da Talita. É o fim da tarde de hoje e ela pediu "
    "um fecho do dia. Use só estas informações da Agenda:\n\n{recap}\n\n"
    "Escreva UM insight curto: 2 a 3 frases, em português do Brasil, informal, em "
    "primeira pessoa, como quem conhece ela. Diga o que o dia revela — o que avançou, "
    "o que travou, o padrão. Nada de lista, nada de título, não comece com emoji e não "
    "dê conselho motivacional. Se o dia foi tranquilo, diga isso sem drama."
)


class BriefingMixin(OrchestratorContract):
    def _start_briefing_worker(self) -> None:
        """Mantido por compatibilidade: quem decide a hora agora e o agendador.

        A automacao ``briefing:*``/``market:watchlist`` vive na tabela
        ``automations`` e o ``_scheduler_loop`` chama o handler. ``self.cfg``
        desligado nao liga nada — e o gate continua existindo (duas camadas).
        """
        cfg = self.cfg.briefing
        if not cfg.enabled or not self.telegram.enabled:
            return
        self.start_scheduler()

    # ------------------------------------------------------- handlers (fase C)
    async def _automation_briefing(
        self,
        item: Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """Briefing da manha ou do meio-dia, no slot declarado na automacao."""
        if not self.cfg.briefing.enabled or not self.telegram.enabled:
            return False
        return await self._send_briefing(item.slot or "morning", now.date())

    async def _automation_evening(
        self,
        item: Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        if not self.cfg.briefing.evening_enabled or not self.telegram.enabled:
            return False
        return await self._send_evening_report(now.date())

    async def _automation_market(
        self,
        item: Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """Carteira da Ariana: deterministica e entregue no painel.

        Nao exige Telegram (armadilha do gate duplo do AGENTS.md): num `serve`
        headless, o resumo vai para o feed do bot e o run guarda o resumo. Com
        Telegram ligado, sai nos dois lugares. Nenhuma chamada ao modelo.
        """
        if not self.cfg.briefing.market_enabled:
            return False
        return await self._send_market_briefing(now.date(), trigger=trigger)

    async def _send_evening_report(self, day: date, *, trigger: str = "schedule") -> bool:
        """Fecha o dia no Telegram uma vez por dia: feito, nao foi e insight."""
        cfg = self.cfg.briefing
        key = f"briefing:{day.isoformat()}:evening"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._briefing_last_attempt < cfg.retry_minutes * 60:
            return False
        if not self.store.claim_meta(key, "sending"):
            return False
        self._briefing_last_attempt = time.monotonic()

        async with record_run(
            self.store,
            kind="briefing",
            automation="briefing:evening",
            trigger=trigger,
            delivery="telegram",
        ) as run:
            return await self._send_evening_body(day, key, run)

    async def _send_evening_body(self, day: date, key: str, run) -> bool:
        try:
            text = await self._compose_evening(day)
        except AgendaError as exc:
            self.logger.warning("fecho da noite: Agenda indisponível: %s", exc)
            self.store.delete_meta(key)
            run.fail(str(exc), "network")
            return False
        except Exception:
            self.store.delete_meta(key)
            raise
        if not text:
            self.store.delete_meta(key)
            run.detail = "sem conteudo para o fecho"
            return False
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("fecho da noite: falha ao enviar no Telegram: %s", exc)
            self.store.delete_meta(key)
            run.fail(str(exc), "network")
            return False

        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        run.detail = "fecho da noite enviado"
        self.logger.info("fecho da noite de %s enviado", day.isoformat())
        return True

    async def _send_briefing(self, slot: str, day: date) -> bool:
        """Envia uma vez por dia/slot; devolve se enviou agora."""
        cfg = self.cfg.briefing
        key = f"briefing:{day.isoformat()}:{slot}"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._briefing_last_attempt < cfg.retry_minutes * 60:
            return False
        # Reserva o slot antes de enviar: TUI e servico do Telegram podem
        # estar abertos ao mesmo tempo e nao devem mandar duas mensagens.
        if not self.store.claim_meta(key, "sending"):
            return False
        self._briefing_last_attempt = time.monotonic()

        async with record_run(
            self.store,
            kind="briefing",
            automation=f"briefing:{slot}",
            delivery="telegram",
        ) as run:
            return await self._send_briefing_body(slot, day, key, run)

    async def _send_briefing_body(self, slot: str, day: date, key: str, run) -> bool:
        try:
            text = await self._compose_briefing(slot, day)
        except AgendaError as exc:
            self.logger.warning("briefing: Agenda indisponível: %s", exc)
            self.store.delete_meta(key)
            run.fail(str(exc), "network")
            return False
        if not text:
            self.store.delete_meta(key)
            run.detail = "sem conteudo"
            return False
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("briefing: falha ao enviar no Telegram: %s", exc)
            self.store.delete_meta(key)
            run.fail(str(exc), "network")
            return False

        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        run.detail = f"briefing {slot} enviado"
        self.logger.info("briefing %s de %s enviado", slot, day.isoformat())
        return True

    async def _send_market_briefing(self, day: date, *, trigger: str = "schedule") -> bool:
        """Monta o resumo da carteira uma vez por dia; devolve se entregou agora."""
        cfg = self.cfg.briefing
        items = self.watchlist.all()
        if not items:
            return False
        key = f"briefing:market:{day.isoformat()}"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._market_last_attempt < cfg.retry_minutes * 60:
            return False
        # Reserva antes de buscar: TUI e servico do Telegram podem coexistir.
        if not self.store.claim_meta(key, "sending"):
            return False
        self._market_last_attempt = time.monotonic()

        async with record_run(
            self.store,
            kind="market",
            automation="market:watchlist",
            trigger=trigger,
            delivery="local",
            bot="ariana",
        ) as run:
            return await self._send_market_body(day, key, items, run)

    async def _send_market_body(self, day: date, key: str, items, run) -> bool:
        results = await asyncio.gather(
            *(fetch_quote(item.ticker) for item in items), return_exceptions=True
        )
        rows = [
            MarketRow(
                label=item.label,
                ticker=item.ticker,
                quote=None if isinstance(result, BaseException) else result,
            )
            for item, result in zip(items, results, strict=False)
        ]
        text = market_message(day, rows)
        indisponiveis = sum(1 for row in rows if row.quote is None)
        # O painel e a entrega principal (design §4.5a): sem Telegram (mini
        # headless) o feed e o run continuam com o resumo inteiro.
        self.bot_message("ariana", "job", "Resumo da carteira", text)
        enviado = False
        if getattr(self.telegram, "enabled", False):
            try:
                await self.telegram.send_message(text)
                enviado = True
            except Exception as exc:
                self.logger.warning("briefing: falha ao enviar a bolsa: %s", exc)
                run.fail(str(exc), "network")
        elif indisponiveis and indisponiveis == len(rows):
            # Sem Telegram e sem cotacao nenhuma: o feed tem o resumo, mas o
            # run precisa dizer por que os numeros faltaram (nunca silencio).
            run.fail(
                "cotacao indisponivel para "
                + ", ".join(row.ticker for row in rows),
                "network",
            )
        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        run.detail = (
            f"{len(rows)} ativo(s)"
            + (f", {indisponiveis} sem cotacao" if indisponiveis else "")
            + (" · telegram" if enviado else " · painel")
        )
        self.logger.info("resumo da bolsa de %s entregue", day.isoformat())
        return True

    async def _compose_briefing(self, slot: str, day: date) -> str:
        cfg = self.cfg.briefing
        if not self.agenda.enabled:
            self.logger.info("briefing ignorado: Agenda desabilitada")
            return ""

        notes = await self.agenda.notes_on(day.isoformat())
        tasks = collect_tasks(notes)

        name = self._user_name()
        note = ""
        if cfg.personalize and slot in cfg.personalize_slots:
            note = await self._personal_note(slot, day, tasks)

        if slot == "morning":
            # A mensagem da manhã traz só as tarefas normais, sem rotinas.
            return morning_message(
                name, day, tasks, max_items=cfg.max_tasks, note=note
            )

        return noon_message(name, day, tasks, max_items=cfg.max_tasks, note=note)

    async def _personal_note(self, slot: str, day: date, tasks) -> str:
        """Uma frase feita pra ela hoje; falha vira string vazia, nunca quebra."""
        cfg = self.cfg.briefing
        try:
            metas = await self._evening_metas()
        except Exception:  # pragma: no cover - defensivo
            metas = []
        done_items, pending_items = partition(tasks)
        pending = [item.text for item in pending_items][:6]
        total = len(done_items) + len(pending_items)
        done = len(done_items)
        meta_lines = "\n".join(f"- {meta}" for meta in metas[:5]) or "- (nenhuma)"
        task_lines = "\n".join(f"- {text}" for text in pending) or "- (nenhuma)"
        context = (
            f"Briefing: {_SLOT_LABEL.get(slot, slot)}\n"
            f"Tarefas pendentes ({len(pending_items)} de {total}):\n{task_lines}\n"
            f"Já feitas hoje: {done}\n"
            f"Metas ativas:\n{meta_lines}"
        )
        prompt = _PERSONAL_NOTE_PROMPT.format(
            hora=datetime.now().astimezone().strftime("%H:%M"),
            weekday=weekday_label(day),
            date=day.strftime("%d/%m/%Y"),
            context=context,
        )
        system = "Você é a Gaia, assistente pessoal da Talita. Seja calorosa, informal e espontânea, sem exageros."
        humor = humor_directive(self.cfg.interface.humor)
        if humor:
            system = f"{system} {humor}"
        session_id = ""
        try:
            session = await self.client.create_session(title=f"briefing {slot} {day.isoformat()}")
            session_id = session.id
            message = await asyncio.wait_for(
                self.client.prompt_sync(session_id, prompt, system=system),
                timeout=max(5.0, cfg.personalize_timeout_seconds),
            )
            return " ".join(str(message.text or "").split()).strip().strip('"').strip("'")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("briefing: comentário pessoal falhou: %s", exc)
            return ""
        finally:
            if session_id:
                try:
                    await self.client.delete_session(session_id)
                except Exception as exc:  # pragma: no cover - depende do provedor
                    self.logger.warning("briefing: falha ao limpar sessão: %s", exc)

    async def _compose_evening(self, day: date) -> str:
        """Lista do dia + insight curto gerado pelo modelo."""
        cfg = self.cfg.briefing
        if not self.agenda.enabled:
            self.logger.info("fecho da noite ignorado: Agenda desabilitada")
            return ""

        notes = await self.agenda.notes_on(day.isoformat())
        tasks = collect_tasks(notes)
        habits, found = collect_habits(notes, cfg.morning_routine)
        if not found:
            routines = await self.agenda.routines()
            habits, _ = habits_from_routines(routines, day, cfg.morning_routine)

        text = evening_message(
            self._user_name(), day, tasks, habits, max_items=cfg.max_tasks
        )
        if cfg.evening_insight:
            metas = await self._evening_metas()
            insight = await self._evening_insight(day, tasks, habits, metas)
            if insight:
                text = f"{text}\n\n{insight}"
        return text

    async def _evening_metas(self) -> list[str]:
        """Metas ativas da Agenda; falha vira lista vazia, nunca quebra o envio."""
        try:
            rows = await self.agenda.metas()
        except Exception as exc:
            self.logger.warning("fecho da noite: metas indisponíveis: %s", exc)
            return []
        return [
            str(row.get("text") or "").strip()
            for row in rows
            if str(row.get("text") or "").strip()
        ]

    async def _evening_insight(
        self, day: date, tasks: list, habits: list, metas: list[str]
    ) -> str:
        """Pede um insight curto do dia ao modelo; falha manda só a lista."""
        cfg = self.cfg.briefing
        prompt = _INSIGHT_PROMPT.format(recap=day_recap(tasks, habits, metas))
        session_id = ""
        try:
            session = await self.client.create_session(
                title=f"insight {day.isoformat()}"
            )
            session_id = session.id
            message = await asyncio.wait_for(
                self.client.prompt_sync(session_id, prompt),
                timeout=max(10.0, cfg.evening_timeout_seconds),
            )
            return str(message.text or "").strip()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("insight da noite falhou: %s", exc)
            return ""
        finally:
            if session_id:
                try:
                    await self.client.delete_session(session_id)
                except Exception as exc:  # pragma: no cover - depende do provedor
                    self.logger.warning("insight: falha ao limpar sessão: %s", exc)
