"""Gate e disparo das rotinas continuas (o que roda sem horario declarado).

Duas responsabilidades so:

* ``_routine_on`` — o ponto unico que responde "esta rotina deve rodar agora?".
  Cruza a pausa da pessoa (tabela ``routines``) com os gates de config do
  registry (``gaia/control/routines.py``). Sem ele cada laco repetiria o mesmo
  ``if`` e um deles ficaria de fora — foi assim que a fase C nasceu.
* ``run_routine_now`` — "rodar agora" da tela, com ``trigger=manual`` e run
  registrado (o disparo automatico **nao** grava run: o laco roda a cada 30 s e
  inundaria a tabela).

Os lacos continuam nos mixins donos (``InitiativeMixin``, ``MemoryMixin``): o
que muda aqui e que a chamada passa por ``_run_routine``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from gaia.control import routines as routines_mod
from gaia.control import runs as runs_mod
from gaia.controller.contract import OrchestratorContract

# id da rotina -> metodo que executa o corpo (so os `runnable` do registry).
_ROUTINE_HANDLERS: dict[str, str] = {
    "initiative:greeting": "_startup_greeting",
    "initiative:reminders": "_fire_due_reminders",
    "initiative:agenda": "_check_agenda",
    "initiative:estudos": "_check_estudos",
    "initiative:tasks": "_check_task_page",
    "initiative:focus": "_check_focus",
    "memory:previous_day": "_consolidate_previous_day",
    "memory:dream_catch_up": "_dream_catch_up",
    "memory:dream_report": "_maybe_dream_report",
}

# Relogios internos que o disparo manual precisa zerar: sem isso "rodar agora"
# nao faz nada quando o throttle do laco acabou de rodar (mesma ideia do
# ``_reset_attempt_clock`` das automacoes).
_ROUTINE_CLOCKS: dict[str, tuple[str, ...]] = {
    "initiative:agenda": ("_agenda_next_check",),
    "initiative:estudos": ("_estudos_next_check",),
    "initiative:tasks": ("_tasks_next_check",),
    "memory:dream_report": ("_dream_report_last_attempt",),
}


class RoutineMixin(OrchestratorContract):
    # ------------------------------------------------------------------ gate
    def _routine_on(
        self, routine_id: str, overrides: dict[str, bool] | None = None
    ) -> bool:
        """A rotina deve rodar? (pausa do painel + gates de config)

        Id desconhecido roda: uma rotina removida do registry num deploy nao
        pode congelar em silencio um laco que ainda a chama.
        """
        item = routines_mod.find(routine_id)
        if item is None:
            return True
        if not routines_mod.config_enabled(self.cfg, item):
            return False
        return routines_mod.is_enabled(self.store, routine_id, overrides)

    def _routine_overrides(self) -> dict[str, bool]:
        """Leitura unica por ciclo do laco (nao uma query por rotina)."""
        return routines_mod.enabled_map(self.store)

    async def _run_routine(
        self,
        routine_id: str,
        handler: Callable[..., Awaitable[Any]],
        now: datetime,
        *,
        overrides: dict[str, bool] | None = None,
    ) -> None:
        """Executa a rotina se o gate permitir (disparo automatico, sem run)."""
        if not self._routine_on(routine_id, overrides):
            return
        await self._call_routine_handler(handler, now)

    # --------------------------------------------------------- disparo manual
    def _routine_handler(self, routine_id: str) -> Any:
        nome = _ROUTINE_HANDLERS.get(routine_id)
        if nome is None:
            return None
        return getattr(self, nome, None)

    async def _call_routine_handler(self, handler: Any, now: datetime) -> None:
        """Chama o handler com ``now`` so quando ele aceita.

        Metade das rotinas recebe o instante (``_check_agenda(now)``) e metade
        nao (``_fire_due_reminders()``, ``_consolidate_previous_day()``).
        Uniformizar as assinaturas quebraria os testes e os chamadores que ja
        existem; a inspecao resolve sem inventar ``lambda`` para cada uma.
        """
        parametros = inspect.signature(handler).parameters
        if parametros:
            await handler(now)
        else:
            await handler()

    def _reset_routine_clock(self, routine_id: str) -> None:
        for atributo in _ROUTINE_CLOCKS.get(routine_id, ()):
            setattr(self, atributo, 0.0)

    async def run_routine_now(self, routine_id: str) -> dict[str, Any]:
        """Roda a rotina agora (``trigger=manual``) e devolve o run registrado.

        Levanta ``KeyError`` para id desconhecido ou rotina sem handler (o
        transporte traduz em 404) e ``RuntimeError`` quando o motor esta num
        turno (409). A pausa do painel **nao** bloqueia: o clique e explicito.
        """
        # A validacao vem antes do "ocupado": um id que nunca vai existir nao
        # deve responder 409 e mandar tentar de novo depois.
        item = routines_mod.find(routine_id)
        handler = self._routine_handler(routine_id)
        if item is None or handler is None:
            raise KeyError(routine_id)
        if self._busy or self._recording or self._listening:
            raise RuntimeError("motor ocupado; tente quando o turno terminar")
        self._reset_routine_clock(routine_id)
        now = datetime.now().astimezone()
        async with runs_mod.record_run(
            self.store,
            kind=item.skill,
            automation=item.id,
            trigger="manual",
            delivery="local",
        ) as run:
            await self._call_routine_handler(handler, now)
            run.detail = f"{item.name} executada a mao"
        return {
            "id": item.id,
            "trigger": "manual",
            "status": run.status,
            "detail": run.detail,
            "error": run.error,
            "error_kind": run.error_kind,
            "attempt": run.attempt,
        }


__all__ = ["RoutineMixin"]
