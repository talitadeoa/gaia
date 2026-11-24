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
from gaia.backlog.repos import validate_repo
from gaia.backlog.service import BacklogService, BacklogUnavailable, fingerprint
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
        on_set_repo: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.agenda = agenda
        self._kick = kick
        # Quem grava o repo e o mixin (o TOML mora no Control Center); aqui so
        # fica o gancho, para o backlog nao importar `gaia.control`.
        self._on_set_repo = on_set_repo

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
            # Projeto sem mapeamento: a tela mostra "sem repositorio" e o item
            # nao despacha ate alguem preencher `[backlog.repos]`.
            grupo["needs_repo"] = not grupo["repo"]
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
            "remove_orphans": bool(self.cfg.remove_orphans),
            "agenda_seen_at": self.store.get_meta("backlog.agenda_seen_at") or "",
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
        """Sincroniza a Agenda com o backlog: gravar, reconciliar, purgar.

        Tres fases, nesta ordem, e a ultima depende das duas primeiras:

        1. **gravar** - cada pagina ``* backlog`` entra com seus itens;
        2. **reconciliar** - grupo sem pagina correspondente foi renomeado
           (mesmo ``page_id`` ou quase o mesmo conjunto de textos) -> move;
        3. **purgar** - o que sobrou sem pagina saiu da Agenda -> apaga.

        Guardas: a purga so roda com ``remove_orphans`` e **depois** de uma
        leitura bem-sucedida (uma excecao em ``pages()`` sobe antes daqui, e
        nada e apagado). Na primeira sincronizacao de todas
        (``backlog.agenda_seen_at`` vazio) o banco so recebe: apagar dado
        legado sem nunca ter lido a Agenda seria perda de trabalho.

        A reconciliacao vem **antes** de gravar os itens: se o grupo foi
        renomeado, os itens antigos chegam em ``gaia2`` com id, status e PR
        intactos, e a gravacao so atualiza o texto. Gravar primeiro criaria
        itens novos (pending) e perderia o vinculo com o PR aberto.
        """
        service = self.service()
        encontradas = inseridos = atualizados = removidos = total = 0
        atuais: dict[str, dict[str, Any]] = {}
        paginas_backlog: list[tuple[str, str, str, list[tuple[str, bool]], bool]] = []
        for pagina in paginas:
            title = str(pagina.get("title") or "")
            projeto, casou = project_from_title(title, self.cfg.agenda_suffix)
            if not casou:
                continue
            page_id = str(pagina.get("id") or "")
            itens = items_from_content(str(pagina.get("content") or ""))
            truncado = len(itens) > int(self.cfg.max_items)
            if truncado:
                itens = itens[: int(self.cfg.max_items)]
            paginas_backlog.append((page_id, projeto, title, itens, truncado))
            atuais[projeto] = {
                "page_id": page_id,
                "fingerprints": {fingerprint(texto) for texto, _ in itens},
            }

        movidos = purgados = 0
        orfaos = service.known_projects() - set(atuais)
        if orfaos:
            movidos, purgados, absorvidos = self._reconciliar(service, orfaos, atuais)
            for projeto in absorvidos:
                # O grupo absorvido nao volta na lista de "atuais": ele deixou
                # de existir (os itens agora sao do novo).
                atuais.pop(projeto, None)

        for page_id, projeto, title, itens, truncado in paginas_backlog:
            resultado = service.sync_page(
                page_id=page_id,
                project=projeto,
                title=title,
                items=itens,
                # Pagina cortada em `max_items`: o que falta e corte, nao linha
                # apagada -- nao pode sumir do card.
                prune=not truncado,
            )
            encontradas += 1
            inseridos += int(resultado["inserted"])
            atualizados += int(resultado["updated"])
            removidos += int(resultado.get("removed") or 0)
            total += int(resultado["total"])
        self._marcar_lida()
        logger.info(
            "backlog: %d pagina(s), %d item(ns) novos, %d atualizados, "
            "%d removidos, %d movidos, %d purgado(s)",
            encontradas,
            inseridos,
            atualizados,
            removidos,
            movidos,
            purgados,
        )
        return {
            "ok": True,
            "pages": encontradas,
            "inserted": inseridos,
            "updated": atualizados,
            "removed": removidos,
            "moved": movidos,
            "purged": purgados,
            "items": total,
            "suffix": self.cfg.agenda_suffix,
        }

    def _reconciliar(
        self,
        service: BacklogService,
        orfaos: set[str],
        atuais: dict[str, dict[str, Any]],
    ) -> tuple[int, int, set[str]]:
        """Decide, para cada grupo sem pagina, entre mover e apagar.

        Devolve ``(movidos, purgados, projetos_absorvidos)``. Renome e
        reconhecido por dois sinais, nesta ordem: o ``page_id`` do banco
        aparece agora num grupo novo (rename mantem o id) ou o grupo novo tem
        praticamente os mesmos textos (>= 80%). Sem sinal, a pagina sumiu.
        """
        if not self.cfg.remove_orphans or not self.store.get_meta("backlog.agenda_seen_at"):
            return 0, 0, set()
        movidos = purgados = 0
        absorvidos: set[str] = set()
        for antigo in sorted(orfaos):
            atual = self._pagina_atual_de(service, antigo, atuais)
            if atual:
                quantos = service.move_project(antigo, atual)
                if quantos:
                    movidos += 1
                    absorvidos.add(antigo)
                    logger.info(
                        "backlog: pagina renomeada, %s -> %s (%d itens)",
                        antigo,
                        atual,
                        quantos,
                    )
                continue
            quantos = service.purge_project(antigo)
            if quantos:
                purgados += 1
                logger.info("backlog: pagina '%s' saiu da Agenda, %d item(ns) apagados", antigo, quantos)
        return movidos, purgados, absorvidos

    def _pagina_atual_de(
        self,
        service: BacklogService,
        antigo: str,
        atuais: dict[str, dict[str, Any]],
    ) -> str:
        """Grupo novo que corresponde a pagina renomeada (``""`` se nenhum)."""
        page_id = ""
        for item in service.list_items(project=antigo, include_concluded=True, limit=1):
            page_id = str(item.get("page_id") or "")
        if page_id:
            for novo, dados in atuais.items():
                if dados.get("page_id") == page_id and novo != antigo:
                    return novo
        meus = service.fingerprints_of(antigo)
        if not meus:
            return ""
        melhor, melhor_taxa = "", 0.0
        for novo, dados in atuais.items():
            dele = set(dados.get("fingerprints") or set())
            if not dele:
                continue
            taxa = len(meus & dele) / len(meus | dele)
            if taxa >= 0.8 and taxa > melhor_taxa:
                melhor, melhor_taxa = novo, taxa
        return melhor

    def _marcar_lida(self) -> None:
        """Marca que a Agenda foi lida: da proxima vez, a purga vale."""
        try:
            self.store.set_meta(
                "backlog.agenda_seen_at",
                datetime.now().astimezone().isoformat(timespec="seconds"),
            )
        except Exception as exc:  # banco sem meta nao pode derrubar o sync
            logger.debug("backlog: nao marquei agenda_seen_at: %s", exc)


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

    def set_repo(self, project: str, repo: str) -> dict[str, Any]:
        """Grava ``backlog.repos.<projeto>`` no TOML (backup) e reflete aqui.

        O contrato do repo (``dono/nome``) é validado pelo ``validate_repo``;
        quem grava é o gancho do mixin, que fala com o ``ControlCenter``.
        """
        nome = " ".join(str(project or "").split())
        if not nome:
            raise ValueError("projeto vazio")
        dono_repo = validate_repo(repo)
        if self._on_set_repo is None:
            raise RuntimeError("motor sem config gravavel: repositorio nao salvo")
        resultado = self._on_set_repo(nome, dono_repo)
        self.cfg.repos[nome] = dono_repo
        return {**resultado, "project": nome, "repo": dono_repo}


__all__ = ["STATUS_LABELS", "BacklogFacade", "project_from_title"]
