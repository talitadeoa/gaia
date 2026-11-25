"""Agendador unico: a agenda da tabela manda, o worker so obedece.

Antes da fase C cada worker decidia sozinho a hora (``if career_due(...)``) e
``gaia/control/automations.py`` descrevia a mesma agenda em paralelo para o
painel — duas fontes de verdade que divergiam em silencio. Aqui existe **um**
laco: a cada N segundos le as automacoes da tabela, calcula ``due()`` e chama o
handler da skill.

Nada de APScheduler, cron do sistema ou segundo banco: continua sendo o
``Orchestrator``, o dono unico do ``gaia.db`` e dos workers.

Duas barreiras contra entrega dupla, de proposito:

1. ``claim_meta("scheduler:<id>:<dia>:<slot>")`` — o tick nao dispara duas
   vezes a mesma janela;
2. ``claim_meta`` dentro da propria rotina (``career:<dia>``,
   ``briefing:<dia>:<slot>``) — intacta desde antes da fase C, cobre o caso de
   dois processos/canais.

Retry (C.5): so ``network`` e ``unknown``, com backoff e teto ``retry_max``.
``auth`` e ``config`` nao repetem — insistir so esconde o erro de quem precisa
agir. Falha final com ``delivery="telegram"`` avisa uma vez no chat (D8).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from gaia.briefing.compose import collect_tasks
from gaia.control import automations as automations_mod
from gaia.control.runs import record_run
from gaia.controller.contract import OrchestratorContract

# id da automacao -> metodo que executa o corpo da rotina.
_AUTOMATION_METHODS: dict[str, str] = {
    "market:watchlist": "_automation_market",
    "briefing:morning": "_automation_briefing",
    "briefing:noon": "_automation_briefing",
    "briefing:evening": "_automation_evening",
    "career:weekly": "_automation_career",
    "memory:daily": "_automation_memory",
    "memory:dream": "_automation_dream",
}

# Automacoes criadas pelo chat (id ``user:*``) nao vem do registry: o handler
# sai da coluna ``skill`` da tabela. Este mapa e a ponte skill -> metodo.
USER_SKILL_METHODS: dict[str, str] = {
    "reminder": "_user_automation_reminder",
    "agenda_summary": "_user_automation_agenda",
    "estudos_summary": "_user_automation_estudos",
    "bot_task": "_user_automation_bot_task",
}

USER_AUTOMATION_PREFIX = "user:"


class SchedulerMixin(OrchestratorContract):
    # --------------------------------------------------------------- ciclo
    def start_scheduler(self) -> None:
        """Liga o agendador unico (idempotente)."""
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        automations_mod.seed(self.cfg, self.store)
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self.logger.info("agendador iniciado (%d automacao(oes))", len(
            automations_mod.load(self.cfg, self.store)
        ))

    def _scheduler_interval(self) -> float:
        """Intervalo de checagem: o mais curto dos workers antigos, >= 5s."""
        valores = [
            float(self.cfg.briefing.check_interval_seconds or 30.0),
            float(self.cfg.career.check_interval_seconds or 30.0),
        ]
        return max(5.0, min(valores))

    async def _scheduler_loop(self) -> None:
        await asyncio.sleep(3.0)
        while True:
            await asyncio.sleep(self._scheduler_interval())
            try:
                await self._scheduler_tick(datetime.now().astimezone())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensivo
                self.logger.warning(
                    "agendador: falha no tick: %s", exc, exc_info=True
                )

    async def _scheduler_tick(self, now: datetime) -> None:
        """Um ciclo: retries vencidos, depois automacoes devidas.

        A ocupacao e POR BOT (design §4.5): o turno da pessoa so bloqueia a
        automacao do bot ativo, e um job de um bot nao espera o outro; dentro
        do mesmo bot nunca ha dois turnos.
        """
        await self._run_due_retries(now)
        for item in automations_mod.load(self.cfg, self.store):
            if not automations_mod.due(item, self.cfg, now, self.store):
                continue
            if self.bot_busy(item.bot):
                continue
            if not automations_mod.claim(item, self.store, now):
                continue
            await self._run_automation(item, now, trigger="schedule", attempt=1)

    # ------------------------------------------------------------- execucao
    def _automation_handler(self, item: automations_mod.Automation):
        # `user:*` e despachado pela SKILL (a mesma automacao pode ser criada
        # varias vezes com horarios diferentes); o resto vem do registry.
        if item.id.startswith(USER_AUTOMATION_PREFIX):
            nome = USER_SKILL_METHODS.get(item.skill)
            if nome is None:
                return None
            return getattr(self, nome, None)
        nome = _AUTOMATION_METHODS.get(item.id)
        if nome is None:
            return None
        return getattr(self, nome, None)

    async def _run_automation(
        self,
        item: automations_mod.Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """Executa um handler e decide retry/notificacao pelo resultado real."""
        handler = self._automation_handler(item)
        if handler is None:
            self.logger.warning("agendador: sem handler para %s", item.id)
            automations_mod.release(item, self.store, now)
            return False
        if trigger == "manual":
            # Pedido explicito: vale mais que o intervalo entre tentativas e
            # que a reserva do dia (a conclusao real continua protegida).
            self._reset_attempt_clock(item)
            chave = item.claim_key(now)
            if chave:
                self.store.delete_meta(chave)
        # O resultado tem de ser o run DESTA execucao: ``last_run`` sozinho
        # confunde uma falha de hoje com o sucesso de ontem (e vice-versa).
        anterior = self._automation_result(item).get("id")
        try:
            ok = bool(
                await handler(item, now, trigger=trigger, attempt=attempt)
            )
        except asyncio.CancelledError:
            automations_mod.release(item, self.store, now)
            raise
        except Exception as exc:  # a rotina ja registra o run; so loga aqui
            self.logger.warning(
                "agendador: %s falhou: %s", item.id, exc, exc_info=True
            )
            ok = False

        resultado = self._automation_result(item)
        if not resultado or resultado.get("id") == anterior:
            # Nada executou: a skill esta desligada na config (ex.: Telegram
            # off) ou nao havia o que fazer. Nao e falha, nao gera retry nem
            # aviso — o run e a prova de execucao real.
            self._retry_plan.pop(item.id, None)
            # Devolve a janela: se a config for ligada durante o dia (ex.:
            # Telegram subiu depois do horario), o proximo tick ainda tenta.
            automations_mod.release(item, self.store, now)
            return ok
        if ok or str(resultado.get("status")) == "completed":
            self._retry_plan.pop(item.id, None)
            return ok

        error_kind = str(resultado.get("error_kind") or "unknown")
        if automations_mod.retry_allowed(error_kind, attempt, item):
            espera = automations_mod.backoff_seconds(item, attempt)
            self._retry_plan[item.id] = (time.monotonic() + espera, attempt + 1)
            self.logger.warning(
                "agendador: %s sera tentada de novo em %.0fs (tentativa %d, %s)",
                item.id,
                espera,
                attempt + 1,
                error_kind,
            )
            await self._notify_automation_failure(item, resultado, now, final=False)
            return False

        self._retry_plan.pop(item.id, None)
        await self._notify_automation_failure(item, resultado, now, final=True)
        return False

    def _automation_result(self, item: automations_mod.Automation) -> dict[str, Any]:
        linha = self.store.last_run(item.skill, item.id)
        return dict(linha or {})

    # ---------------------------------------------- automacoes do chat (v1)
    async def _user_automation_reminder(
        self,
        item: automations_mod.Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """Lembrete criado pelo chat: entrega o texto na hora marcada.

        Nao passa por ``_fire_due_reminders``: esse laco le a tabela
        ``reminders`` (prazo unico) e aqui a recorrencia e da automacao. O run
        registra a execucao real; a reserva da janela e do agendador.
        """
        if not self.cfg.proactive.reminders:
            return False
        texto = str(item.params.get("text") or "").strip() or item.name
        async with record_run(
            self.store,
            kind="reminder",
            automation=item.id,
            trigger=trigger,
            delivery=item.delivery,
            attempt=attempt,
        ) as run:
            await self._say_proactive(f"Lembrete: {texto}", count=False)
            run.detail = f"lembrete entregue ({item.delivery})"
        return True

    async def _user_automation_agenda(
        self,
        item: automations_mod.Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """Resumo da Agenda do dia (pendentes e feitas), sem usar o modelo."""
        if not self.cfg.agenda.enabled or not self.agenda.enabled:
            return False
        async with record_run(
            self.store,
            kind="agenda_summary",
            automation=item.id,
            trigger=trigger,
            delivery=item.delivery,
            attempt=attempt,
        ) as run:
            notes = await self.agenda.notes_on(now.date().isoformat())
            tarefas = collect_tasks(notes)
            pendentes = [t for t in tarefas if not t.done]
            feitas = [t for t in tarefas if t.done]
            limite = max(1, int(self.cfg.agenda.max_items))
            linhas = [
                f"Resumo da agenda de {now.strftime('%d/%m')}",
                f"Pendentes: {len(pendentes)} · feitas: {len(feitas)}",
            ]
            linhas.extend(f"- ☐ {t.text}" for t in pendentes[:limite])
            if not pendentes:
                linhas.append("- nada pendente")
            await self._say_proactive("\n".join(linhas), count=False)
            run.detail = f"{len(pendentes)} pendente(s), {len(feitas)} feita(s)"
        return True

    async def _user_automation_estudos(
        self,
        item: automations_mod.Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """Resumo do progresso dos simulados (Abin Suite), sem usar o modelo."""
        if not self.cfg.estudos.enabled or not self.estudos.enabled:
            return False
        from gaia.estudos.progresso import (
            formatar_resumo,
            resumo_semana,
        )

        async with record_run(
            self.store,
            kind="estudos_summary",
            automation=item.id,
            trigger=trigger,
            delivery=item.delivery,
            attempt=attempt,
        ) as run:
            results = await self.estudos.results()
            goals = await self.estudos.goals()
            resumo = resumo_semana(results, goals, now=now)
            estado = "estudou hoje" if resumo.estudou_hoje else "ainda nao estudou hoje"
            marca = (
                "meta em dia"
                if not resumo.atrasado
                else "meta atrasada"
            )
            texto = (
                f"Estudos nesta semana: {formatar_resumo(resumo)}. "
                f"{estado}; {marca}."
            )
            await self._say_proactive(texto, count=False)
            run.detail = formatar_resumo(resumo)
        return True

    def _reset_attempt_clock(self, item: automations_mod.Automation) -> None:
        """Zera o intervalo entre tentativas da rotina (so no disparo manual)."""
        atributos = {
            "career:weekly": "_career_last_attempt",
            "market:watchlist": "_market_last_attempt",
            "briefing:morning": "_briefing_last_attempt",
            "briefing:noon": "_briefing_last_attempt",
            "briefing:evening": "_briefing_last_attempt",
        }
        atributo = atributos.get(item.id)
        if atributo:
            setattr(self, atributo, 0.0)

    async def _automation_bot_task(
        self,
        item: automations_mod.Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """Segmento comum das automacoes por prompt (registry ou `user:*`)."""
        return await self._run_bot_task_item(
            item,
            prompt=str(item.params.get("prompt") or item.name),
            trigger=trigger,
            attempt=attempt,
        )

    async def _user_automation_bot_task(
        self,
        item: automations_mod.Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        """`user:*` criada pelo chat com skill `bot_task` (prompt no bot)."""
        return await self._automation_bot_task(
            item, now, trigger=trigger, attempt=attempt
        )

    async def _run_bot_task_item(
        self,
        item: automations_mod.Automation,
        *,
        prompt: str,
        trigger: str,
        attempt: int,
    ) -> bool:
        """Segundo plano por prompt: o run e o feed do bot sao a entrega.

        Sem Telegram tambem funciona: a saida principal e `bot_messages` (o
        painel). Falha ja vira mensagem no feed e run `failed` com
        `error_kind`, entao o agendador decide retry sozinho.
        """
        try:
            resultado = await self.run_bot_task(
                item.bot or "gaia",
                prompt,
                title=item.name,
                automation_id=item.id,
                trigger=trigger,
                attempt=attempt,
            )
        except Exception as exc:
            self.logger.warning("bot_task %s falhou: %s", item.id, exc)
            return False
        return bool(resultado.get("ok"))

    async def _run_due_retries(self, now: datetime) -> None:
        for automation_id, (prazo, tentativa) in list(self._retry_plan.items()):
            if time.monotonic() < prazo:
                continue
            item = automations_mod.find_loaded(self.cfg, self.store, automation_id)
            self._retry_plan.pop(automation_id, None)
            if item is None:
                continue
            if self.bot_busy(item.bot):
                # So a sessao daquele bot esta ocupada: a tentativa espera.
                self._retry_plan[automation_id] = (time.monotonic(), tentativa)
                continue
            await self._run_automation(item, now, trigger="retry", attempt=tentativa)

    async def _notify_automation_failure(
        self,
        item: automations_mod.Automation,
        resultado: dict[str, Any],
        now: datetime,
        *,
        final: bool,
    ) -> None:
        """Avisa no chat uma vez por falha (D8), e so para entrega telegram.

        ``claim_meta`` garante que um restart nao repita o aviso; falha local
        (memoria/sonho) fica so no Error Center.
        """
        if item.delivery != "telegram":
            return
        if not getattr(self.telegram, "enabled", False):
            return
        chave = f"automation_failed:{item.id}:{now.date().isoformat()}"
        if not self.store.claim_meta(chave, now.isoformat(timespec="seconds")):
            return
        motivo = str(resultado.get("error") or "erro sem detalhe").strip()
        tipo = str(resultado.get("error_kind") or "unknown")
        sufixo = "" if final else " Vou tentar de novo."
        texto = f"{item.name} falhou ({tipo}): {motivo[:200]}.{sufixo}"
        try:
            await self.telegram.send_message(texto)
        except Exception as exc:  # aviso nao pode derrubar o agendador
            self.logger.warning(
                "agendador: falha ao avisar sobre %s: %s", item.id, exc
            )

    # ------------------------------------------------------- disparo manual
    async def run_automation_now(self, automation_id: str) -> dict[str, Any]:
        """Executa agora (trigger=manual) e devolve o run registrado.

        Levanta ``KeyError`` para id desconhecido e ``RuntimeError`` quando o
        motor esta ocupado (o transporte traduz em 409).
        """
        item = automations_mod.find_loaded(self.cfg, self.store, automation_id)
        if item is None:
            raise KeyError(automation_id)
        if self._busy or self._recording or self._listening:
            raise RuntimeError("motor ocupado; tente quando o turno terminar")
        now = datetime.now().astimezone()
        await self._run_automation(item, now, trigger="manual")
        resultado = self._automation_result(item)
        return {
            "id": automation_id,
            "trigger": "manual",
            "status": str(resultado.get("status") or "unknown"),
            "detail": str(resultado.get("detail") or ""),
            "error": str(resultado.get("error") or ""),
            "error_kind": str(resultado.get("error_kind") or ""),
            "attempt": int(resultado.get("attempt") or 1),
        }


__all__ = ["SchedulerMixin"]
