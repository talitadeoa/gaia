"""Fachada sincrona do backlog, usada pelas ferramentas e pelo comando.

O mixin do Orchestrator e assincrono (fala com a Agenda e com o loop do
motor); as ferramentas do agente sao sincronas e rodam em thread. Esta fachada
esconde a diferenca:

* ``sync()`` cria um event loop proprio (``asyncio.run``) para ler a Agenda,
  igual as ferramentas de ``agenda_*``;
* ``run()`` so marca o item como ``queued`` e avisa o loop do motor para
  acordar o worker (``kick``). Quem executa o pi e o worker, nunca a ferramenta.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from gaia.agenda.client import AgendaClient
from gaia.backlog.extract import items_from_content
from gaia.backlog.service import BacklogService, BacklogUnavailable
from gaia.config.models import BacklogConfig

logger = logging.getLogger("gaia.backlog")

# Rotulos de status que a tela mostra (o JSON leva o codigo e o rotulo).
STATUS_LABELS = {
    "pending": "Pendente",
    "queued": "Enviado ao Mini PC",
    "running": "Em execucao no Pi",
    "pr": "PR aberto",
    "done": "Concluido",
    "failed": "Erro",
}


def project_from_title(title: str, suffix: str) -> tuple[str, bool]:
    """``(projeto, casou)`` a partir do titulo da pagina.

    ``"gaia backlog"`` -> ``("gaia", True)``. Pagina sem o sufixo nao e fonte
    de backlog (o ``casou`` e o que o chamador usa para filtrar).
    """
    limpo = " ".join(str(title or "").split())
    alvo = " ".join(str(suffix or "backlog").split()).lower()
    if not alvo or not limpo.lower().endswith(alvo):
        return limpo, False
    # hifens e travessoes que sobram: "gaia - backlog" -> "gaia".
    projeto = limpo[: -len(alvo)].strip(" -\u2013\u2014_")
    return (projeto or limpo, True)


class BacklogFacade:
    """Operacoes do backlog sem depender do loop do motor."""

    def __init__(
        self,
        cfg: BacklogConfig,
        store: Any,
        *,
        agenda: AgendaClient | None = None,
        kick: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.agenda = agenda
        self._kick = kick

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled)

    def service(self) -> BacklogService:
        return BacklogService(self.store, self.cfg.prune_days)

    def set_kick(self, kick: Callable[[], None] | None) -> None:
        """Liga a fachada ao worker (o mixin chama isto ao subir a fila)."""
        self._kick = kick

    # ------------------------------------------------------------------ leitura
    def snapshot(self) -> dict[str, Any]:
        try:
            service = self.service()
            grupos = service.pages()
            contadores = service.counts()
        except BacklogUnavailable as exc:
            return {
                "enabled": self.enabled,
                "reason": str(exc),
                "groups": [],
                "items": [],
                "counts": {},
                "labels": STATUS_LABELS,
            }
        for grupo in grupos:
            grupo["repo"] = self.cfg.repo_for(str(grupo.get("project") or ""))
            for item in grupo["items"]:
                item["status_label"] = STATUS_LABELS.get(str(item.get("status")), "")
        return {
            "enabled": self.enabled,
            "reason": "" if self.enabled else "backlog desligado (backlog.enabled = false)",
            "groups": grupos,
            "items": [
                dict(item, status_label=STATUS_LABELS.get(str(item.get("status")), ""))
                for grupo in grupos
                for item in grupo["items"]
            ],
            "counts": contadores,
            "labels": STATUS_LABELS,
            "repos": dict(self.cfg.repos),
            "prune_days": self.cfg.prune_days,
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def list_items(self, include_concluded: bool = True) -> list[dict[str, Any]]:
        try:
            return self.service().list_items(include_concluded=include_concluded)
        except BacklogUnavailable:
            return []

    # ---------------------------------------------------------- sincronizacao
    def sync(self) -> dict[str, Any]:
        """Le a Agenda (loop proprio) e grava os itens. Retorna os contadores."""
        if not self.enabled:
            raise RuntimeError("backlog desligado (backlog.enabled = false)")
        if self.agenda is None or not getattr(self.agenda, "enabled", False):
            raise RuntimeError(
                "Agenda desabilitada: configure agenda.env_file com as credenciais "
                "do Supabase para sincronizar as paginas de backlog"
            )
        paginas = asyncio.run(self.agenda.pages(max(1, int(self.cfg.agenda_scan))))
        return self._gravar_paginas(paginas)

    async def sync_async(self) -> dict[str, Any]:
        """Versao para quem ja esta num loop (o mixin do Orchestrator)."""
        if not self.enabled:
            raise RuntimeError("backlog desligado (backlog.enabled = false)")
        if self.agenda is None or not getattr(self.agenda, "enabled", False):
            raise RuntimeError(
                "Agenda desabilitada: configure agenda.env_file com as credenciais "
                "do Supabase para sincronizar as paginas de backlog"
            )
        paginas = await self.agenda.pages(max(1, int(self.cfg.agenda_scan)))
        return self._gravar_paginas(paginas)

    def _gravar_paginas(self, paginas: list[dict[str, Any]]) -> dict[str, Any]:
        service = self.service()
        encontradas = inseridos = atualizados = total = 0
        for pagina in paginas:
            title = str(pagina.get("title") or "")
            projeto, casou = project_from_title(title, self.cfg.agenda_suffix)
            if not casou:
                continue
            itens = items_from_content(str(pagina.get("content") or ""))
            if len(itens) > int(self.cfg.max_items):
                itens = itens[: int(self.cfg.max_items)]
            resultado = service.sync_page(
                page_id=str(pagina.get("id") or ""),
                project=projeto,
                title=title,
                items=itens,
            )
            encontradas += 1
            inseridos += int(resultado["inserted"])
            atualizados += int(resultado["updated"])
            total += int(resultado["total"])
        logger.info(
            "backlog: %d pagina(s), %d item(ns) novos, %d atualizados",
            encontradas,
            inseridos,
            atualizados,
        )
        return {
            "ok": True,
            "pages": encontradas,
            "inserted": inseridos,
            "updated": atualizados,
            "items": total,
            "suffix": self.cfg.agenda_suffix,
        }

    # ----------------------------------------------------------------- escrita
    def add(self, text: str, project: str = "") -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("backlog desligado (backlog.enabled = false)")
        return self.service().add_chat_item(text, project=project)

    def run(self, item_id: int) -> dict[str, Any]:
        """Enfileira o item (quem executa e o worker do motor)."""
        if not self.enabled:
            raise RuntimeError("backlog desligado (backlog.enabled = false)")
        item = self.service().mark_queued(int(item_id))
        if self._kick is not None:
            try:
                self._kick()
            except Exception as exc:  # motor sem loop: o polling pega
                logger.debug("backlog: kick falhou: %s", exc)
        return item

    def remove(self, item_id: int) -> bool:
        if not self.enabled:
            raise RuntimeError("backlog desligado (backlog.enabled = false)")
        return self.service().delete(int(item_id))


__all__ = ["STATUS_LABELS", "BacklogFacade", "project_from_title"]
