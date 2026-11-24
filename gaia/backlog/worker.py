"""Fila serial do backlog: um item por vez, no loop do motor.

O estado da fila e o proprio banco (``status='queued'``): reiniciar o
``gaia serve`` nao perde o pedido nem dispara duas vezes. O worker acorda por
evento (quando alguem despacha) e por tempo (a cada ``POLL_SECONDS``), e roda o
``BacklogJob`` em thread (git/gh/pi sao subprocessos bloqueantes).

Duas garantias:

* **serial** - ``max_running`` (padrao 1): dois itens no mesmo clone brigariam
  pelo branch. O segundo espera na fila.
* **notifica uma vez** - o aviso de PR pronto sai no fim da execucao do item;
  falha avisa com o motivo. ``notify`` e injetado pelo ``Orchestrator`` (que
  sabe se o Telegram esta ligado).

A limpeza de 7 dias roda uma vez por dia aqui (``prune``), nao na leitura do
card: a rota nao paga o custo e o item de chat concluido nao volta.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from gaia.backlog.job import BacklogJob, BacklogResult
from gaia.backlog.service import BacklogService

logger = logging.getLogger("gaia.backlog")

# Intervalo do tique: item despachado pelo chat e pego em ate ~5s.
POLL_SECONDS = 5.0
# Uma limpeza por dia e suficiente para "some depois de 7 dias".
PRUNE_INTERVAL_SECONDS = 6 * 60 * 60

Notifier = Callable[[str, dict[str, Any], BacklogResult], Any]


class BacklogWorker:
    """Consome ``queued`` do banco e leva cada item ate o PR."""

    def __init__(
        self,
        service: BacklogService,
        job_factory: Callable[[dict[str, Any]], BacklogJob],
        *,
        notify: Notifier | None = None,
        enabled: bool = True,
        on_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.service = service
        self.job_factory = job_factory
        self.notify = notify
        self.enabled = enabled
        self.on_change = on_change
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._ultima_limpeza = 0.0
        self.processando: int | None = None

    # ------------------------------------------------------------------ ciclo
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Liga o laco (idempotente; precisa estar no loop do motor)."""
        if self.running:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        tarefa = self._task
        self._task = None
        if tarefa is not None:
            tarefa.cancel()
            try:
                await tarefa
            except (asyncio.CancelledError, Exception):
                pass

    def kick(self) -> None:
        """Acorda o laco agora (item novo na fila). Seguro fora do loop."""
        try:
            self._wake.set()
        except RuntimeError:  # pragma: no cover - loop diferente
            pass

    async def _loop(self) -> None:
        # Espera o motor terminar de subir (o clone pode nem existir ainda).
        await asyncio.sleep(2.0)
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensivo
                logger.warning("backlog: falha no tique: %s", exc, exc_info=True)

    async def _tick(self) -> None:
        if not self.enabled:
            return
        await self._maybe_prune()
        # So `running` conta para o limite: contar `queued` travava a fila (o
        # primeiro item enfileirado nunca era pego).
        if self.service.running_count() >= self._max_running():
            return
        item = self.service.next_queued()
        if item is None:
            return
        await self.run_item(item)

    def _max_running(self) -> int:
        # Um por vez e o padrao; mais de um exigiria clones separados por item.
        return 1

    async def _maybe_prune(self) -> None:
        import time

        agora = time.monotonic()
        if agora - self._ultima_limpeza < PRUNE_INTERVAL_SECONDS:
            return
        self._ultima_limpeza = agora
        removidos = await asyncio.to_thread(self.service.prune)
        if removidos:
            logger.info("backlog: %d item(ns) de chat antigos removidos", removidos)

    # -------------------------------------------------------------- execucao
    async def run_item(self, item: dict[str, Any]) -> BacklogResult:
        """Executa um item ja enfileirado (marca running -> pr|failed)."""
        item_id = int(item.get("id") or 0)
        self.processando = item_id
        self.service.mark_running(
            item_id,
            branch=str(item.get("branch") or ""),
            repo=str(item.get("repo") or ""),
        )
        self._avisar("backlog_started", item_id)
        try:
            job = self.job_factory(item)
            resultado = await asyncio.to_thread(job.execute, item)
        except Exception as exc:  # job_factory quebrado nao pode travar a fila
            logger.warning("backlog: item %s quebrou: %s", item_id, exc, exc_info=True)
            resultado = BacklogResult(ok=False, error=f"falha ao montar o job: {exc}")
        finally:
            self.processando = None

        if resultado.ok:
            self.service.mark_pr(
                item_id,
                pr_url=resultado.pr_url,
                pr_number=resultado.pr_number,
                detail=resultado.detail,
            )
        else:
            self.service.mark_failed(
                item_id, resultado.error or "falhou sem mensagem", detail=resultado.detail
            )
        atual = self.service.get(item_id) or item
        self._avisar("backlog_finished", item_id, atual, resultado)
        await self._notificar(item_id, atual, resultado)
        return resultado

    def _avisar(
        self,
        tipo: str,
        item_id: int,
        item: dict[str, Any] | None = None,
        resultado: BacklogResult | None = None,
    ) -> None:
        """Gancho para a UI emitir evento (o web escuta por SSE)."""
        if self.on_change is None:
            return
        try:
            self.on_change(
                {
                    "type": tipo,
                    "id": item_id,
                    "item": item or {},
                    "ok": bool(resultado.ok) if resultado else False,
                }
            )
        except Exception as exc:  # notificacao nao derruba a execucao
            logger.debug("backlog: on_change falhou: %s", exc)

    async def _notificar(
        self, item_id: int, item: dict[str, Any], resultado: BacklogResult
    ) -> None:
        if self.notify is None:
            return
        try:
            retorno = self.notify(str(item.get("status") or ""), item, resultado)
            if asyncio.iscoroutine(retorno):
                await retorno
        except Exception as exc:  # aviso nunca derruba o worker
            logger.warning("backlog: falha ao avisar sobre o item %s: %s", item_id, exc)

    # --------------------------------------------------------------- despacho
    def enqueue(self, item_id: int) -> dict[str, Any]:
        """Marca o item como ``queued`` e acorda o worker (levanta KeyError)."""
        if not self.enabled:
            raise RuntimeError("backlog desligado (backlog.enabled = false)")
        item = self.service.mark_queued(item_id)
        self.kick()
        return item

    def describe(self) -> dict[str, Any]:
        """Estado do worker para o cabecalho do card."""
        return {
            "enabled": bool(self.enabled),
            "running": self.running,
            "processing": self.processando,
            "prune_days": self.service.prune_days,
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
        }


__all__ = ["POLL_SECONDS", "PRUNE_INTERVAL_SECONDS", "BacklogWorker"]
