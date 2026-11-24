"""Historico de execucao das rotinas: contexto, classificacao e leitura.

O registro existe para responder "isso rodou de verdade?" — iniciar nao e
sucesso. Por isso ``record_run`` so e usado **depois** do gate de horario e da
reserva (``claim_meta``) de cada rotina.

Registrar nunca pode derrubar a rotina: qualquer falha de banco aqui vira
``warning`` no log e a execucao segue.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from gaia.memory.store import MemoryStore

logger = logging.getLogger("gaia.control")

# Codigos/termos que apontam para credencial ou saldo: o problema e humano,
# nao de rede, e o retry automatico nao resolve.
_AUTH_PATTERNS = (
    r"\b401\b",
    r"\b402\b",
    r"\b403\b",
    r"unauthorized",
    r"insufficient (balance|quota|credits?)",
    r"invalid[_ ]api[_ ]key",
    r"authentication",
    r"forbidden",
    r"sem saldo",
    r"chave invalida",
)
_NETWORK_PATTERNS = (
    r"timeout",
    r"timed out",
    r"tempo limite",
    r"connection",
    r"conexao",
    r"network",
    r"rede",
    r"dns",
    r"ssl",
    r"httpx",
    r"unreachable",
    r"temporarily unavailable",
    r"\b50[234]\b",
)
_CONFIG_PATTERNS = (
    r"nao configurad",
    r"nao definid",
    r"missing",
    r"obrigatori",
    r"invalid config",
    r"enabled = false",
    r"desligad",
)


def classify_error(message: str) -> str:
    """Traduz a mensagem em ``auth|network|config|unknown``.

    Serve para decidir retry (so network/unknown) e para a UI explicar a
    origem do problema sem mostrar stack trace.
    """
    texto = (message or "").strip().lower()
    if not texto:
        return "unknown"
    for padr in _CONFIG_PATTERNS:
        if re.search(padr, texto):
            return "config"
    for padr in _AUTH_PATTERNS:
        if re.search(padr, texto):
            return "auth"
    for padr in _NETWORK_PATTERNS:
        if re.search(padr, texto):
            return "network"
    return "unknown"


@dataclass
class RunHandle:
    """Estado mutavel de uma execucao em andamento.

    ``detail`` e o resumo humano ("3 vagas", "briefing morning"); a rotina
    preenche o que ja ia para o log.
    """

    id: int = 0
    kind: str = ""
    automation: str = ""
    trigger: str = "schedule"
    delivery: str = ""
    attempt: int = 1
    detail: str = ""
    error: str = ""
    error_kind: str = ""
    status: str = "running"
    finished: bool = False
    # Atribuicao da execucao (vazio = `gaia`, o comportamento anterior aos
    # bots). O painel agrupa o feed por este campo.
    bot: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def fail(self, message: str, kind: str = "") -> None:
        self.status = "failed"
        self.error = message
        self.error_kind = kind or classify_error(message)


@asynccontextmanager
async def record_run(
    store: MemoryStore,
    *,
    kind: str,
    automation: str = "",
    trigger: str = "schedule",
    delivery: str = "",
    attempt: int = 1,
    bot: str = "",
) -> AsyncIterator[RunHandle]:
    """Registra inicio/fim de uma execucao real de rotina.

    Excecao levanta -> run fechado como ``failed`` com o erro classificado e
    a excecao propagada (a rotina decide o que fazer). Cancelamento vira
    ``cancelled``. Falha ao gravar nunca interrompe a rotina.
    """
    run = RunHandle(
        kind=kind,
        automation=automation,
        trigger=trigger,
        delivery=delivery,
        attempt=attempt,
        bot=bot,
    )
    try:
        run.id = store.begin_run(
            kind=kind,
            automation=automation,
            trigger=trigger,
            delivery=delivery,
            attempt=attempt,
            bot=bot,
        )
    except Exception as exc:  # banco travado nao pode matar a rotina
        logger.warning("run: nao foi possivel abrir a execucao de %s: %s", kind, exc)
        run.id = 0

    try:
        yield run
    except BaseException as exc:
        run.fail(str(exc) or exc.__class__.__name__)
        if isinstance(exc, asyncio.CancelledError):
            run.status = "cancelled"
        _close(store, run)
        raise
    else:
        if run.status == "running":
            run.status = "completed"
        _close(store, run)


def _close(store: MemoryStore, run: RunHandle) -> None:
    if run.finished:
        return
    run.finished = True
    if not run.id:
        return
    try:
        store.finish_run(
            run.id,
            status=run.status,
            detail=run.detail,
            error=run.error,
            error_kind=run.error_kind,
        )
    except Exception as exc:  # pragma: no cover - banco indisponivel
        logger.warning("run: nao foi possivel fechar a execucao %s: %s", run.id, exc)


def render_run(row: dict[str, Any]) -> dict[str, Any]:
    """Normaliza uma linha de ``runs`` para JSON (sem campos internos)."""
    return {
        "id": int(row.get("id") or 0),
        "kind": str(row.get("kind") or ""),
        "automation": str(row.get("automation") or ""),
        "bot": str(row.get("bot") or ""),
        "trigger": str(row.get("trigger") or ""),
        "status": str(row.get("status") or ""),
        "started_at": str(row.get("started_at") or ""),
        "finished_at": str(row.get("finished_at") or ""),
        "duration_ms": int(row.get("duration_ms") or 0),
        "delivery": str(row.get("delivery") or ""),
        "detail": str(row.get("detail") or ""),
        "error": str(row.get("error") or ""),
        "error_kind": str(row.get("error_kind") or ""),
        "attempt": int(row.get("attempt") or 1),
    }


def recent_runs(store: MemoryStore, limit: int = 20) -> list[dict[str, Any]]:
    return [render_run(row) for row in store.list_runs(limit=limit)]


def summary_since(store: MemoryStore, days: int = 1) -> dict[str, Any]:
    since = (datetime.now().astimezone() - timedelta(days=max(1, days))).isoformat(
        timespec="seconds"
    )
    return store.runs_summary(since)
