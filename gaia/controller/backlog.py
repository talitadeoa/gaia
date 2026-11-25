"""Backlog do painel: liga o card do web ao worker e as fontes.

O estado mora no SQLite (``backlog_items``, schema v8) e a regra pura esta em
``gaia/backlog/`` (``facade``, ``job``, ``worker``). Este mixin e a cola com o
``Orchestrator``:

* ``start_backlog`` cria o worker no loop do motor (um item por vez);
* ``backlog_sync`` le as paginas da Agenda cujo titulo termina no sufixo
  configurado (``gaia backlog``, ``agenda backlog``, ``suite backlog``);
* ``backlog_add``/``backlog_enqueue`` alimentam a fila (chat/voz e card);
* ``_handle_backlog`` e o comando ``/backlog`` (terminal, Telegram e web).

O aviso de PR pronto passa por ``ui.proactive`` e pelo Telegram quando ele
esta ligado no mesmo processo.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gaia.agenda.client import AgendaError
from gaia.backlog.facade import STATUS_LABELS, BacklogFacade, project_from_title
from gaia.backlog.job import BacklogJob, BacklogResult
from gaia.backlog.worker import BacklogWorker
from gaia.controller.contract import OrchestratorContract

logger = logging.getLogger("gaia.backlog")


class BacklogMixin(OrchestratorContract):
    # ------------------------------------------------------------------ estado
    def backlog_enabled(self) -> bool:
        return bool(getattr(self.cfg.backlog, "enabled", False))

    def backlog_service_facade(self) -> BacklogFacade:
        """Fachada ligada ao mesmo banco (e ao worker, quando ele existe)."""
        worker = self._backlog_worker
        fachada = BacklogFacade(
            self.cfg.backlog,
            self.store,
            agenda=self.agenda,
            kick=(worker.kick if worker is not None else None),
            on_set_repo=self._backlog_gravar_repo,
        )
        return fachada

    def _backlog_job(self, item: dict[str, Any]) -> BacklogJob:
        return BacklogJob(self.cfg.backlog, self.cfg.pi)

    def start_backlog(self) -> BacklogWorker | None:
        """Liga a fila do backlog (idempotente; precisa do loop do motor)."""
        if not self.backlog_enabled():
            return None
        if self._backlog_worker is not None and self._backlog_worker.running:
            return self._backlog_worker
        if not self.store.backlog_ready():
            logger.warning("backlog: banco sem a tabela backlog_items")
            return None
        self._backlog_worker = BacklogWorker(
            self.backlog_service_facade().service(),
            self._backlog_job,
            notify=self._backlog_notify,
            enabled=True,
            on_change=self._backlog_change,
        )
        self._backlog_worker.start()
        return self._backlog_worker

    async def stop_backlog(self) -> None:
        worker = self._backlog_worker
        self._backlog_worker = None
        if worker is not None:
            await worker.stop()

    def _backlog_change(self, evento: dict[str, Any]) -> None:
        """Evento SSE para a tela atualizar o card sem esperar o polling."""
        emitir = getattr(self.ui, "emit", None)
        if callable(emitir):
            emitir("backlog", **evento)

    async def _backlog_notify(
        self, status: str, item: dict[str, Any], resultado: BacklogResult
    ) -> None:
        """Avisa quando o PR fica pronto (ou quando o item falha)."""
        # Rotina `backlog:notify` pausada no painel: o worker nunca avisa.
        if not self._routine_on("backlog:notify"):
            logger.debug("backlog: aviso desligado no painel")
            return
        titulo = str(item.get("title") or "item do backlog")
        projeto = str(item.get("project") or "backlog")
        if resultado.ok:
            texto = (
                f"Backlog: PR pronto para '{titulo}' ({projeto}).\n"
                f"{resultado.pr_url or ''}"
            ).strip()
        else:
            texto = (
                f"Backlog: o item '{titulo}' ({projeto}) falhou: "
                f"{resultado.error or 'erro sem detalhe'}"
            )
        # O feed da Ada e a entrega do painel (design §4.5a/§7 item 13): o PR
        # aberto (ou a falha) aparece no rail sem depender do Telegram.
        self.bot_message(
            "ada",
            "job",
            f"PR pronto: {titulo}" if resultado.ok else f"Falhou: {titulo}",
            texto,
        )
        try:
            self.ui.proactive(texto)
        except Exception as exc:  # UI nunca derruba o worker
            logger.debug("backlog: ui.proactive falhou: %s", exc)
        enviar = getattr(self.telegram, "send_message", None)
        if getattr(self.telegram, "enabled", False) and callable(enviar):
            try:
                await enviar(texto)
            except Exception as exc:
                logger.warning("backlog: falha ao avisar no Telegram: %s", exc)

    # --------------------------------------------------------------- leitura
    def backlog_snapshot(self) -> dict[str, Any]:
        """JSON do card: grupos por projeto, contadores e estado do worker."""
        dados = self.backlog_service_facade().snapshot()
        worker = self._backlog_worker
        dados["worker"] = worker.describe() if worker is not None else {
            "enabled": self.backlog_enabled(),
            "running": False,
            "processing": None,
            "prune_days": self.cfg.backlog.prune_days,
        }
        dados["agenda"] = {
            "enabled": bool(getattr(self.agenda, "enabled", False)),
            "suffix": self.cfg.backlog.agenda_suffix,
        }
        return dados

    # ---------------------------------------------------------- sincronizacao
    async def backlog_sync(self) -> dict[str, Any]:
        """Le as paginas de backlog na Agenda e atualiza os itens."""
        try:
            return await self.backlog_service_facade().sync_async()
        except RuntimeError as exc:
            # "backlog desligado"/"Agenda desabilitada": a rota traduz em 503.
            raise AgendaError(str(exc)) from exc

    # ------------------------------------------------------------------ escrita
    def backlog_add(self, text: str, project: str = "") -> dict[str, Any]:
        """Item novo pelo chat/voz. Devolve o item ja gravado."""
        item = self.backlog_service_facade().add(text, project=project)
        self._backlog_change({"type": "backlog_added", "id": item.get("id")})
        return item

    def backlog_enqueue(self, item_id: int) -> dict[str, Any]:
        """Enfileira o item para o mini/pi (levanta KeyError se nao existe)."""
        worker = self.start_backlog()
        if worker is None:
            raise RuntimeError("fila do backlog indisponivel neste motor")
        fachada = self.backlog_service_facade()
        item = fachada.service().get(int(item_id))
        if item is None:
            raise KeyError(item_id)
        if str(item.get("status")) in {"running", "queued"}:
            return item
        atual = worker.enqueue(int(item_id))
        return {**atual, "status_label": STATUS_LABELS.get(str(atual.get("status")), "")}

    def backlog_remove(self, item_id: int) -> bool:
        return self.backlog_service_facade().remove(int(item_id))

    def _backlog_gravar_repo(self, project: str, repo: str) -> dict[str, Any]:
        """Grava o par em ``[backlog.repos]`` do TOML, com backup e conferencia.

        O ``ControlCenter`` ja tem a maquinaria (`settings.update`: backup,
        releitura, rollback). Se nao houver config para editar (teste/motor sem
        TOML), o repo fica **so** no processo e o retorno diz isso -- nunca
        fingir que persistiu.
        """
        from gaia.control import settings as settings_mod

        novo = dict(self.cfg.backlog.repos)
        novo[project] = repo
        caminho = Path(self.cfg.config_file or "").expanduser()
        if not str(self.cfg.config_file or "").strip():
            self.cfg.backlog.repos[project] = repo
            return {"saved": False, "path": "", "backup": ""}
        resultado = settings_mod.update(
            caminho, "backlog", "repos", novo, cfg=self.cfg
        )
        self.cfg.backlog.repos[project] = repo
        self._backlog_change({"type": "backlog_repo", "project": project, "repo": repo})
        return {"saved": True, **resultado}

    def backlog_set_repo(self, project: str, repo: str) -> dict[str, Any]:
        """Projeto sem repositorio: salva o mapa e destrava o despacho."""
        try:
            return self.backlog_service_facade().set_repo(project, repo)
        except ValueError:
            raise
        except RuntimeError as exc:
            raise AgendaError(str(exc)) from exc

    # ------------------------------------------------------------------ comando
    async def _handle_backlog(self, argument: str) -> None:
        """Comando ``/backlog`` (terminal, Telegram e chat do web).

        Subcomandos: vazio = lista; ``sync`` = consulta a Agenda;
        ``add [projeto:] texto`` = item do chat; ``run <id>`` = despacha;
        ``repo <projeto> <dono/repo>`` = grava o repositorio no TOML;
        ``remover <id>`` = apaga o item.
        """
        partes = argument.strip().split(maxsplit=1)
        sub = partes[0].lower() if partes else ""
        resto = partes[1].strip() if len(partes) > 1 else ""
        if not self.backlog_enabled():
            self.ui.warning("backlog desligado (backlog.enabled = false)")
            return
        if sub in {"sync", "sincronizar", "atualizar"}:
            try:
                resultado = await self.backlog_sync()
            except (AgendaError, RuntimeError) as exc:
                self.ui.error(str(exc))
                return
            self.ui.success(
                f"backlog: {resultado['pages']} pagina(s), "
                f"{resultado['inserted']} novo(s), {resultado['updated']} atualizado(s)"
                f"{', ' + str(resultado['removed']) + ' removido(s)' if resultado.get('removed') else ''}"
                f"{', ' + str(resultado['moved']) + ' grupo(s) renomeado(s)' if resultado.get('moved') else ''}"
                f"{', ' + str(resultado['purged']) + ' grupo(s) sem pagina' if resultado.get('purged') else ''}"
            )
            return
        if sub in {"add", "novo", "adicionar"}:
            if not resto:
                self.ui.warning("uso: /backlog add [projeto:] texto")
                return
            projeto, texto = "", resto
            cabeca, _, corpo = resto.partition(":")
            if corpo and " " not in cabeca.strip():
                projeto, texto = cabeca.strip(), corpo.strip()
            try:
                item = self.backlog_add(texto, projeto)
            except (ValueError, RuntimeError) as exc:
                self.ui.error(str(exc))
                return
            self.ui.success(
                f"backlog #{item.get('id')} ({item.get('project') or 'chat'}): "
                f"{item.get('title')}"
            )
            return
        if sub in {"run", "executar", "enviar"}:
            try:
                item = self.backlog_enqueue(int(resto))
            except (ValueError, KeyError, RuntimeError):
                self.ui.warning("uso: /backlog run <id> (veja o id em /backlog)")
                return
            self.ui.success(f"backlog #{item.get('id')} na fila do mini")
            return
        if sub in {"remover", "remove", "apagar"}:
            try:
                removido = self.backlog_remove(int(resto))
            except (ValueError, RuntimeError):
                self.ui.warning("uso: /backlog remover <id>")
                return
            self.ui.info("removido" if removido else "item nao encontrado")
            return
        if sub in {"repo", "repositorio", "repositório"}:
            partes_repo = resto.split(maxsplit=1)
            if len(partes_repo) < 2:
                self.ui.warning("uso: /backlog repo <projeto> <dono/repo>")
                return
            try:
                salvo = self.backlog_set_repo(partes_repo[0], partes_repo[1])
            except (ValueError, RuntimeError, AgendaError) as exc:
                self.ui.error(str(exc))
                return
            if salvo.get("saved"):
                self.ui.success(
                    f"backlog: {salvo['project']} = {salvo['repo']} "
                    f"(gravado em {salvo.get('path') or 'config.toml'})"
                )
            else:
                self.ui.warning(
                    f"backlog: {salvo['project']} = {salvo['repo']} "
                    "(sem config.toml gravavel: vale so neste processo)"
                )
            return
        snapshot = self.backlog_snapshot()
        grupos = snapshot.get("groups") or []
        if not grupos:
            self.ui.info("backlog vazio; use /backlog sync ou /backlog add <texto>")
            return
        linhas = ["Backlog:"]
        for grupo in grupos:
            linhas.append(f"[{grupo.get('project')}] ({grupo.get('repo')})")
            for item in grupo["items"]:
                linhas.append(
                    f"  #{item.get('id')} [{item.get('status')}] {item.get('title')}"
                )
        self.ui.info("\n".join(linhas))


__all__ = ["STATUS_LABELS", "BacklogMixin", "project_from_title"]
