"""Rotina de memoria diaria: consolida o dia e expõe o comando /consolidar."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta

from gaia.control.automations import Automation
from gaia.control.runs import record_run
from gaia.controller.contract import OrchestratorContract
from gaia.controller.proactive import in_quiet_hours
from gaia.memory.consolidation import DailyMemory, parse_day
from gaia.memory.dream import Dreamer, dream_recap, dream_target_day, in_dream_window


class MemoryMixin(OrchestratorContract):
    def _daily_memory(self) -> DailyMemory:
        cache = getattr(self, "_daily_memory_cache", None)
        if cache is None:
            cache = DailyMemory(
                self.cfg,
                self.store,
                self.brain,
                self.index,
                self.client,
                self.directory,
                self.logger,
            )
            self._daily_memory_cache = cache
        return cache

    async def consolidate_day(
        self,
        day: date | None = None,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> str | None:
        return await self._daily_memory().consolidate(
            day, force=force, timeout_seconds=timeout_seconds
        )

    def _dreamer(self) -> Dreamer:
        cache = getattr(self, "_dreamer_cache", None)
        if cache is None:
            cache = Dreamer(
                self.cfg,
                self.store,
                self.brain,
                self.index,
                self.client,
                self.directory,
                self.logger,
            )
            self._dreamer_cache = cache
        return cache

    async def dream_day(
        self,
        day: date | None = None,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> str | None:
        return await self._dreamer().dream(
            day, force=force, timeout_seconds=timeout_seconds
        )

    async def _maybe_consolidate(self, now: datetime, *, trigger: str = "schedule") -> None:
        """Consolida o dia (chamado pelo handler da automacao ``memory:daily``)."""
        cfg = self.cfg.memory
        if not cfg.enabled:
            return
        if now.strftime("%H:%M") < cfg.at:
            return
        day = now.date()
        async with record_run(
            self.store,
            kind="memory",
            automation="memory:daily",
            trigger=trigger,
            delivery="local",
        ) as run:
            path = await self.consolidate_day(day)
            run.detail = "memoria do dia consolidada" if path else "nada a consolidar"

    async def _maybe_dream(self, now: datetime, *, trigger: str = "schedule") -> None:
        """Sonha de madrugada, depois da consolidacao e quando ociosa.

        A janela e ``[dream_at, dream_until)`` e pode virar a meia-noite: o
        padrao (02:00 -> 08:00) sonha sobre o dia que acabou. Ficar fora da
        janela nao perde o sonho: ele entra na proxima madrugada, ainda sobre
        o mesmo dia, porque a guarda e por dia sonhado.
        """
        cfg = self.cfg.memory
        if not cfg.enabled or not cfg.dream_enabled:
            return
        if not in_dream_window(now, cfg.dream_at, cfg.dream_until):
            return
        day = dream_target_day(now)
        # O sonho costura o dia com o passado; garante a memoria do dia antes.
        await self.consolidate_day(day)
        async with record_run(
            self.store,
            kind="dream",
            automation="memory:dream",
            trigger=trigger,
            delivery="local",
        ) as run:
            path = await self.dream_day(day)
            run.detail = "sonho escrito" if path else "nada a sonhar"

    async def _maybe_dream_report(self, now: datetime) -> None:
        """Avisa sobre o sonho da noite passada, uma vez, no dia seguinte.

        Sai pelo canal em uso (TUI fala, Telegram manda no chat) e nao conta
        como iniciativa: ``count=False`` mantem o ``daily_max`` da proatividade
        intacto, senao o sonho roubaria um dos avisos do dia. Respeita as quiet
        hours, entao um reboot as 3h nao acorda ninguem; o aviso espera.
        """
        cfg = self.cfg.memory
        if not cfg.enabled or not cfg.dream_enabled or not cfg.dream_notify:
            return
        # /proativo off e silencio total: o aviso nao fura a escolha dela.
        if not self.cfg.proactive.enabled:
            return
        if now.strftime("%H:%M") < cfg.dream_notify_at:
            return
        if in_quiet_hours(now, self.cfg.proactive.quiet_hours):
            return
        day = dream_target_day(now)
        path = self.store.get_meta(f"memory_dreamed:{day.isoformat()}")
        if not path:
            return
        key = f"memory_dream_reported:{day.isoformat()}"
        # Se o envio falhar, nao adianta tentar a cada 60s: espera o mesmo
        # espaco do briefing entre tentativas.
        if time.monotonic() - self._dream_report_last_attempt < cfg.retry_minutes * 60:
            return
        # Reserva antes de falar: TUI e servico do Telegram podem coexistir.
        if not self.store.claim_meta(key, "sending"):
            return
        self._dream_report_last_attempt = time.monotonic()
        try:
            note = await asyncio.to_thread(self.brain.read_note, str(path))
            aviso = dream_recap(day, note)
        except Exception as exc:
            self.logger.warning("sonho: nao consegui ler a nota para avisar: %s", exc)
            self.store.delete_meta(key)
            return
        if not aviso:
            self.logger.warning(
                "sonho de %s sem frase nem pergunta: aviso nao enviado", day.isoformat()
            )
            self.store.delete_meta(key)
            return
        try:
            await self._say_proactive(aviso, count=False)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - depende da interface
            self.logger.warning("sonho: falha ao avisar: %s", exc)
            self.store.delete_meta(key)
            return
        self.store.set_meta(key, now.isoformat(timespec="seconds"))
        self.logger.info("sonho: aviso de %s enviado", day.isoformat())

    async def _dream_catch_up(self) -> None:
        """Sonha o dia que ficou sem sonho se a Gaia estava desligada as 2h.

        So vale de manha (antes do meio-dia): depois disso, sonhar com a noite
        que passou seria escrever a madrugada no meio da tarde. Dentro da janela
        normal o laco cuida sozinho, entao aqui nao se faz nada.
        """
        cfg = self.cfg.memory
        if not cfg.enabled or not cfg.dream_enabled:
            return
        now = datetime.now().astimezone()
        if now.hour >= 12:
            return
        if in_dream_window(now, cfg.dream_at, cfg.dream_until):
            return
        day = dream_target_day(now)
        if self.store.get_meta(f"memory_dreamed:{day.isoformat()}"):
            return
        await self.consolidate_day(day)
        await self.dream_day(day)

    async def _consolidate_previous_day(self) -> None:
        """Recupera o dia anterior se a Gaia nao estava aberta na hora certa."""
        if not self.cfg.memory.enabled:
            return
        yesterday = datetime.now().astimezone().date() - timedelta(days=1)
        await self.consolidate_day(yesterday)

    # ------------------------------------------------------- handlers (fase C)
    async def _automation_memory(
        self,
        item: Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        if not self.cfg.memory.enabled:
            return False
        antes = self.store.get_meta(f"memory_consolidated:{now.date().isoformat()}")
        await self._maybe_consolidate(now, trigger=trigger)
        depois = self.store.get_meta(f"memory_consolidated:{now.date().isoformat()}")
        # Nada a consolidar nao e falha: a conclusao (ou a ausencia de turnos)
        # ja foi registrada no run. O que importa e nao marcar retry.
        return bool(depois) or bool(antes)

    async def _automation_dream(
        self,
        item: Automation,
        now: datetime,
        *,
        trigger: str = "schedule",
        attempt: int = 1,
    ) -> bool:
        if not self.cfg.memory.enabled or not self.cfg.memory.dream_enabled:
            return False
        day = dream_target_day(now)
        antes = self.store.get_meta(f"memory_dreamed:{day.isoformat()}")
        await self._maybe_dream(now, trigger=trigger)
        depois = self.store.get_meta(f"memory_dreamed:{day.isoformat()}")
        return bool(depois) or bool(antes)

    def _start_memory_worker(self) -> None:
        """Liga a rotina de memoria (catch-up + aviso do sonho).

        A consolidacao e o sonho sao automacoes governaveis (fase C): quem
        decide a hora e o ``_scheduler_loop``. Este worker fica com o que nao
        tem horario declarado: recuperar o dia anterior e o sonho perdido no
        boot, e o aviso do sonho no dia seguinte.
        """
        if not self.cfg.memory.enabled or self._memory_task is not None:
            return
        self._memory_task = asyncio.create_task(self._memory_loop())

    async def _memory_loop(self) -> None:
        try:
            if self._routine_on("memory:previous_day"):
                await self._consolidate_previous_day()
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("consolidação do dia anterior falhou: %s", exc)
        try:
            if self._routine_on("memory:dream_catch_up"):
                await self._dream_catch_up()
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("sonho atrasado falhou: %s", exc)
        while True:
            await asyncio.sleep(60.0)
            if not self.cfg.memory.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            try:
                now = datetime.now().astimezone()
                # Consolidacao, sonho e briefings saem pelo agendador unico;
                # aqui so o aviso do sonho (sem horario declarado na tabela).
                await self._run_routine(
                    "memory:dream_report", self._maybe_dream_report, now
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - depende do provedor
                self.logger.warning("falha na memória diária: %s", exc)

    async def _handle_consolidate(self, argument: str) -> None:
        day, force = parse_day(argument)
        self.ui.info(f"consolidando a memória de {day.isoformat()}…")
        try:
            path = await self.consolidate_day(
                day, force=force, timeout_seconds=self.cfg.memory.llm_timeout_seconds
            )
        except Exception as exc:
            self.ui.error(f"falha ao consolidar: {exc}")
            return
        if path:
            self.ui.success(f"memória do dia salva em {path}")
        else:
            self.ui.info("nada novo para consolidar (ou já consolidado)")

    async def discard_dream(self, day: date) -> str | None:
        """Esquece o sonho de um dia: nota para a lixeira e dia marcado.

        E o equivalente ao "descarta" da revisao de fatos. A marca
        ``memory_dream_forgotten:<dia>`` impede que o catch-up da manha sonhe o
        mesmo dia outra vez, o que tornaria o descarte inutil.
        """
        path = self.store.get_meta(f"memory_dreamed:{day.isoformat()}")
        if not path:
            return None
        moved = await asyncio.to_thread(
            self.brain.delete_note, str(path), confirm=True
        )
        self.store.delete_meta(f"memory_dreamed:{day.isoformat()}")
        self.store.delete_meta(f"memory_dream_reported:{day.isoformat()}")
        self.store.set_meta(
            f"memory_dream_forgotten:{day.isoformat()}",
            datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        self.logger.info("sonho de %s descartado em %s", day.isoformat(), moved)
        return str(moved)

    async def _handle_dream(self, argument: str) -> None:
        if "descart" in (argument or "").lower():
            limpo = re.sub(r"\bdescart\w*", " ", argument or "", flags=re.IGNORECASE)
            day, _force = parse_day(limpo)
            try:
                moved = await self.discard_dream(day)
            except Exception as exc:
                self.ui.error(f"falha ao descartar o sonho: {exc}")
                return
            if moved:
                self.ui.success(f"sonho de {day.isoformat()} descartado (lixeira: {moved})")
            else:
                self.ui.info(f"não há sonho de {day.isoformat()} para descartar")
            return
        day, force = parse_day(argument)
        self.ui.info(f"sonhando sobre {day.isoformat()}…")
        try:
            await self.consolidate_day(day)
            path = await self.dream_day(
                day, force=force, timeout_seconds=self.cfg.memory.llm_timeout_seconds
            )
        except Exception as exc:
            self.ui.error(f"falha ao sonhar: {exc}")
            return
        if path:
            self.ui.success(f"sonho salvo em {path}")
            # Pedido na mao conta como ja visto: sem isso o laco de memoria
            # mandaria o mesmo aviso no chat logo depois, em duplicidade.
            self.store.set_meta(
                f"memory_dream_reported:{day.isoformat()}",
                datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            try:
                note = self.brain.read_note(path)
                aviso = dream_recap(day, note)
            except Exception:
                aviso = ""
            if aviso:
                self.ui.print_markdown(aviso, "Sonho")
        else:
            self.ui.info("nada para sonhar (ou já sonhado)")
