"""Rotina semanal de vagas no Telegram, sem LLM.

A hora vem do agendador unico (``controller/scheduler.py``): a automacao
``career:weekly`` vive na tabela ``automations`` e chama ``_automation_career``.
Aqui fica so o corpo: reserva o dia com ``claim_meta`` para TUI e servico do
Telegram nao enviarem duas vezes, e busca as fontes so quando o dia ainda nao
foi entregue.
"""

from __future__ import annotations

import time
from datetime import date, datetime

from gaia.career.service import (
    collect,
    compose_message,
    load_state,
    mark_delivered,
    pending_jobs,
    record_results,
    save_state,
    state_path,
)
from gaia.control.automations import Automation
from gaia.control.runs import record_run
from gaia.controller.contract import OrchestratorContract


class CareerMixin(OrchestratorContract):
    def _career_state_path(self):
        return state_path(
            self.cfg.resolved_data_dir(),
            self.cfg.career.state_file,
        )

    def _start_career_worker(self) -> None:
        """Mantido por compatibilidade: a hora vem do agendador unico.

        O gate de horario saiu daqui (fase C): a automacao ``career:weekly``
        vive na tabela ``automations`` e o ``_scheduler_loop`` chama
        ``_automation_career``. ``career.enabled``/``telegram.enabled``
        continuam valendo (segunda camada, sem excecao).
        """
        cfg = self.cfg.career
        if not cfg.enabled or not self.telegram.enabled:
            return
        self.start_scheduler()

    # ------------------------------------------------------- handler (fase C)
    async def _automation_career(
        self,
        item: Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        if not self.cfg.career.enabled or not self.telegram.enabled:
            return False
        return await self._send_career(now.date(), trigger=trigger)

    async def _send_career(self, day: date, *, trigger: str = "schedule") -> bool:
        """Busca, registra e entrega o que estiver pendente; devolve se enviou."""
        cfg = self.cfg.career
        key = f"career:{day.isoformat()}"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._career_last_attempt < cfg.retry_minutes * 60:
            return False
        # Reserva antes de buscar: a rodada custa rede e nao pode rodar duas vezes.
        if not self.store.claim_meta(key, "sending"):
            return False
        self._career_last_attempt = time.monotonic()

        path = self._career_state_path()
        async with record_run(
            self.store,
            kind="career",
            automation="career:weekly",
            trigger=trigger,
            delivery="telegram",
            bot="sabrina",
        ) as run:
            return await self._send_career_body(day, key, path, run)

    async def _send_career_body(self, day: date, key: str, path, run) -> bool:
        """Corpo real da rotina, separado para o registro de execucao.**"""
        cfg = self.cfg.career
        try:
            state = load_state(path)
        except ValueError as exc:
            self.logger.warning("rotina de vagas: %s", exc)
            self.store.delete_meta(key)
            run.fail(str(exc), "config")
            return False

        result = await collect(state)
        # Grava antes de enviar: se o Telegram falhar, as vagas ficam pendentes
        # e a proxima rodada entrega em vez de perde-las.
        try:
            record_results(state, result)
            save_state(path, state)
        except OSError as exc:
            self.logger.warning("rotina de vagas: falha ao gravar o estado: %s", exc)
            self.store.delete_meta(key)
            run.fail(str(exc), "unknown")
            return False

        pendentes = pending_jobs(state)
        if not pendentes and not result.bruto:
            # Nada a entregar e nenhuma fonte respondeu: tenta de novo depois.
            self.store.delete_meta(key)
            run.detail = "sem vagas novas; fontes nao responderam"
            return False

        text = compose_message(state, result=result, day=day, limit=cfg.max_items)
        ensinar = [str(item.get("id")) for item in pendentes] if pendentes else []
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("rotina de vagas: falha ao enviar no Telegram: %s", exc)
            self.store.delete_meta(key)
            run.fail(str(exc), "network")
            return False

        if ensinar:
            mark_delivered(state, ensinar)
            try:
                save_state(path, state)
            except OSError as exc:  # pragma: no cover - disco cheio
                self.logger.warning("rotina de vagas: entrega nao registrada: %s", exc)

        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        run.detail = (
            f"{len(ensinar)} entregues de {result.bruto} lidas"
            if ensinar
            else f"{result.bruto} lidas, nada novo"
        )
        self.logger.info(
            "vagas de %s enviadas: %d entregues de %d lidas",
            day.isoformat(),
            len(ensinar),
            result.bruto,
        )
        return True
