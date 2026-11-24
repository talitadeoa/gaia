"""Estado do backlog no SQLite: uma linha por item, a fonte de verdade.

A tabela ``backlog_items`` (schema v8) guarda o item como ele foi lido da
fonte e o estado do despacho:

* ``status``: ``pending`` -> ``queued`` -> ``running`` -> ``pr`` | ``failed``.
  ``done`` e o estado humano de "entregue em PR"; a Agenda tambem pode marcar
  o item como concluido (``source_done``) e a tela mostra isso.
* ``source``: ``agenda`` (pagina sincronizada) ou ``chat`` (adicionado por
  texto/voz). O item de chat nao tem ``page_id``.
* ``project``: a **chave do grupo** derivada do titulo da pagina
  (``gaia backlog`` -> ``gaia``). E ela que identifica a origem, nao o
  ``page_id`` do Supabase: renomear a pagina nao perde o item.
* ``page_id``: referencia da pagina (atualizada a cada sincronizacao, usada
  para reconhecer renome e para o prompt do pi).
* ``fingerprint``: identidade estavel dentro da fonte (texto normalizado), para
  sincronizar duas vezes sem duplicar nem perder o estado do item.
* ``finished_at``: quando chegou em ``pr``/``done``; a limpeza de 7 dias usa
  essa coluna (e nunca apaga o historico, so esconde da tela).

A pagina e a fonte: ``purge_project`` apaga o grupo cuja pagina sumiu da
Agenda e ``move_project`` leva os itens quando ela foi renomeada. Os dois sao
chamados pela fachada **depois** de uma leitura bem-sucedida da Agenda (falha
de rede nunca apaga nada).

A limpeza e de *visualizacao*: quem consulta o card recebe
``completed_before`` e o SQL filtra. Nada e apagado do banco.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from typing import Any

STATUSES = ("pending", "queued", "running", "pr", "done", "failed")
# Estados que a limpeza de 7 dias pode esconder (concluido/implementado).
CONCLUDED = ("pr", "done")
# Estados que contam como "trabalho em andamento" para a fila.
ACTIVE = ("queued", "running")


class BacklogUnavailable(RuntimeError):
    """Banco sem a tabela do backlog (migracao antiga): a rota avisa."""


def fingerprint(text: str) -> str:
    """Identidade do item dentro da fonte: texto sem caixa/acento/pontuacao."""
    bruto = unicodedata.normalize("NFKD", str(text or "").lower())
    limpo = "".join(ch for ch in bruto if not unicodedata.combining(ch))
    limpo = re.sub(r"[^a-z0-9 ]+", " ", limpo)
    limpo = " ".join(limpo.split())
    return hashlib.sha1(limpo.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class BacklogService:
    """Leitura/escrita dos itens. Sincrono: as rotas rodam na thread do HTTP."""

    def __init__(self, store: Any, prune_days: int = 7):
        # `store` e o MemoryStore (mesma conexao/conexao do motor).
        self.store = store
        self.prune_days = max(0, int(prune_days or 0))

    # ------------------------------------------------------------------ schema
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self.store, "backlog_ready") or not self.store.backlog_ready():
            raise BacklogUnavailable("banco sem a tabela backlog_items (schema antigo)")
        return self.store._conn

    # --------------------------------------------------------------- leitura
    def cutoff(self) -> str:
        """Instante antes do qual um item concluido some da tela."""
        if self.prune_days <= 0:
            return ""
        quando = datetime.now().astimezone() - timedelta(days=self.prune_days)
        return quando.isoformat(timespec="seconds")

    def list_items(
        self,
        *,
        include_concluded: bool = True,
        page_id: str = "",
        project: str = "",
        status: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Itens visiveis (os concluidos ha mais de ``prune_days`` ficam fora)."""
        clausulas: list[str] = []
        valores: list[Any] = []
        corte = self.cutoff()
        if corte and include_concluded:
            # Antigo = concluido E finalizado antes do corte. Item concluido na
            # Agenda mas nunca despachado (finished_at vazio) continua visivel:
            # sumir com ele esconderia trabalho que ninguem executou.
            clausulas.append(
                "(status IN ('pending','queued','running','failed') "
                "OR finished_at = '' OR finished_at >= ?)"
            )
            valores.append(corte)
        if page_id:
            clausulas.append("page_id = ?")
            valores.append(page_id)
        if project:
            clausulas.append("project = ?")
            valores.append(project)
        if status:
            clausulas.append("status = ?")
            valores.append(status)
        where = f"WHERE {' AND '.join(clausulas)}" if clausulas else ""
        valores.append(max(1, min(int(limit), 2000)))
        with self.store._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM backlog_items {where} ORDER BY id ASC LIMIT ?",
                valores,
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, item_id: int) -> dict[str, Any] | None:
        with self.store._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM backlog_items WHERE id = ?", (int(item_id),)
            ).fetchone()
        return dict(row) if row else None

    def find_by_fingerprint(
        self, *, source: str, key: str, fingerprint: str, by: str = "project"
    ) -> dict[str, Any] | None:
        """Item da mesma origem com o mesmo texto (``key`` = project ou page_id)."""
        coluna = "page_id" if by == "page_id" else "project"
        with self.store._lock, self._conn() as conn:
            row = conn.execute(
                f"SELECT * FROM backlog_items WHERE source = ? AND {coluna} = ? "
                "AND fingerprint = ?",
                (source, key, fingerprint),
            ).fetchone()
        return dict(row) if row else None

    def counts(self, include_concluded: bool = True) -> dict[str, int]:
        itens = self.list_items(include_concluded=include_concluded, limit=2000)
        total: dict[str, int] = {chave: 0 for chave in STATUSES}
        for item in itens:
            chave = str(item.get("status") or "pending")
            total[chave] = total.get(chave, 0) + 1
        total["total"] = len(itens)
        return total

    def active_count(self) -> int:
        with self.store._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM backlog_items "
                "WHERE status IN ('queued','running')"
            ).fetchone()
        return int(row["total"] if row else 0)

    def next_queued(self) -> dict[str, Any] | None:
        with self.store._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM backlog_items WHERE status = 'queued' "
                "ORDER BY queued_at ASC, id ASC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # --------------------------------------------------------------- escrita
    def add_chat_item(
        self, text: str, *, project: str = "", now: datetime | None = None
    ) -> dict[str, Any]:
        """Item adicionado pelo chat/voz. Texto vazio e recusado."""
        limpo = " ".join(str(text or "").split())
        if not limpo:
            raise ValueError("item sem texto")
        marca = fingerprint(limpo)
        existente = self.find_by_fingerprint(
            source="chat", key="", fingerprint=marca, by="page_id"
        )
        if existente is not None:
            return existente
        momento = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
        with self.store._lock, self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO backlog_items
                    (source, page_id, project, title, status, fingerprint,
                     created_at, updated_at)
                VALUES ('chat', '', ?, ?, 'pending', ?, ?, ?)
                """,
                (project.strip(), limpo, marca, momento, momento),
            )
            novo = int(cursor.lastrowid or 0)
        item = self.get(novo)
        assert item is not None
        return item

    def sync_page(
        self,
        *,
        page_id: str,
        project: str,
        title: str,
        items: list[tuple[str, bool]],
        now: datetime | None = None,
        prune: bool = True,
    ) -> dict[str, int]:
        """Grava os itens da pagina preservando o estado de cada um.

        A identidade do item e ``(project, fingerprint)`` dentro da fonte
        ``agenda`` -- **nao** o ``page_id``: renomear a pagina na Agenda muda o
        id, mas o grupo e o texto continuam os mesmos, entao o item mantem
        status/PR em vez de nascer de novo. O ``page_id`` e gravado como
        referencia (o prompt do pi cita a pagina).

        Itens que ja existem tem o titulo/estado de origem atualizados, mas
        **nunca** voltam de ``pr`` para ``pending``: o despacho e mais forte
        que a marcacao da pagina.

        ``prune=True`` espelha a pagina tambem no nivel do item: linha que
        sumiu da pagina (ou foi reescrita, mudando o texto) sai do backlog. Sem
        isso o card guardaria para sempre o que a pessoa apagou. Ficam de fora
        ``pr``/``done`` (historico do PR aberto, ja escondido por
        ``prune_days``) e o chamador desliga o prune quando truncou a pagina em
        ``max_items`` -- ali o que falta nao e remocao, e corte.
        """
        momento = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
        inseridos = 0
        atualizados = 0
        vistos: set[str] = set()
        with self.store._lock, self._conn() as conn:
            for texto, concluido in items:
                marca = fingerprint(texto)
                vistos.add(marca)
                row = conn.execute(
                    "SELECT id, status, source_done, finished_at FROM backlog_items "
                    "WHERE source = 'agenda' AND project = ? AND fingerprint = ?",
                    (project, marca),
                ).fetchone()
                if row is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO backlog_items
                            (source, page_id, project, title, status,
                             source_done, fingerprint, created_at, updated_at)
                        VALUES ('agenda', ?, ?, ?, 'pending', ?, ?, ?, ?)
                        """,
                        (
                            page_id,
                            project,
                            texto,
                            int(bool(concluido)),
                            marca,
                            momento,
                            momento,
                        ),
                    )
                    inseridos += 1
                    if concluido:
                        # Concluido na pagina antes de qualquer despacho: e
                        # "done" para a limpeza poder agir.
                        conn.execute(
                            "UPDATE backlog_items SET status = 'done', "
                            "finished_at = ? WHERE id = ?",
                            (momento, int(cursor.lastrowid or 0)),
                        )
                    continue
                status = str(row["status"] or "pending")
                campos: dict[str, Any] = {
                    "title": texto,
                    "page_id": page_id,
                    "source_done": int(bool(concluido)),
                    "updated_at": momento,
                }
                if concluido and status == "pending":
                    campos["status"] = "done"
                    campos["finished_at"] = str(row["finished_at"] or momento)
                elif not concluido and status == "done":
                    # Desmarcou na pagina: volta a pendente (nao mexe em pr).
                    campos["status"] = "pending"
                    campos["finished_at"] = ""
                conn.execute(
                    f"UPDATE backlog_items SET {', '.join(f'{k} = ?' for k in campos)} "
                    "WHERE id = ?",
                    (*campos.values(), int(row["id"])),
                )
                atualizados += 1
            removidos = 0
            if prune:
                removidos = self._apagar_ausentes(conn, project, vistos)
        return {
            "inserted": inseridos,
            "updated": atualizados,
            "removed": removidos,
            "total": len(items),
        }

    @staticmethod
    def _apagar_ausentes(
        conn: sqlite3.Connection, project: str, vistos: set[str]
    ) -> int:
        """Linhas do grupo que nao estao mais na pagina (preserva pr/done)."""
        rows = conn.execute(
            "SELECT id, fingerprint, status FROM backlog_items "
            "WHERE source = 'agenda' AND project = ?",
            (str(project),),
        ).fetchall()
        alvos = [
            int(row["id"])
            for row in rows
            if str(row["fingerprint"]) not in vistos
            and str(row["status"]) not in CONCLUDED
        ]
        for item_id in alvos:
            conn.execute("DELETE FROM backlog_items WHERE id = ?", (item_id,))
        return len(alvos)

    # ------------------------------------------------------ reconciliacao
    def known_projects(self) -> set[str]:
        """Grupos de pagina que existem hoje no banco (fonte ``agenda``)."""
        with self.store._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT project FROM backlog_items WHERE source = 'agenda'"
            ).fetchall()
        return {str(row["project"]) for row in rows if str(row["project"] or "")}

    def project_of_page(self, page_id: str) -> str:
        """Grupo que referencia esta pagina (para reconhecer renome)."""
        with self.store._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT project FROM backlog_items WHERE source = 'agenda' "
                "AND page_id = ? ORDER BY id ASC LIMIT 1",
                (str(page_id),),
            ).fetchone()
        return str(row["project"]) if row else ""

    def fingerprints_of(self, project: str) -> set[str]:
        """Textos (normalizados) de um grupo, para casar a pagina renomeada."""
        with self.store._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT fingerprint FROM backlog_items "
                "WHERE source = 'agenda' AND project = ?",
                (project,),
            ).fetchall()
        return {str(row["fingerprint"]) for row in rows if row["fingerprint"]}

    def purge_project(self, project: str) -> int:
        """Apaga o grupo cuja pagina saiu da Agenda; devolve quantos itens.

        Apaga **todos** os estados, inclusive ``pr``/``done``: sem pagina o
        grupo nao deveria aparecer. O PR continua no GitHub; o card e da
        Agenda. Item de ``source='chat'`` nunca e tocado (nao tem pagina).
        """
        with self.store._lock, self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM backlog_items WHERE source = 'agenda' AND project = ?",
                (str(project),),
            )
            return int(cursor.rowcount or 0)

    def move_project(self, old: str, new: str) -> int:
        """Leva os itens de ``old`` para ``new`` (pagina renomeada).

        Preserva id, status, PR e ordem. Se o grupo novo ja tem um item com o
        mesmo texto, o duplicado (o mais recente) sai antes do update, senao
        a mesma linha apareceria duas vezes na tela.
        """
        if not old or not new or old == new:
            return 0
        with self.store._lock, self._conn() as conn:
            novos = {
                str(row["fingerprint"]): int(row["id"])
                for row in conn.execute(
                    "SELECT id, fingerprint FROM backlog_items "
                    "WHERE source = 'agenda' AND project = ?",
                    (new,),
                ).fetchall()
            }
            antigos = conn.execute(
                "SELECT id, fingerprint FROM backlog_items "
                "WHERE source = 'agenda' AND project = ?",
                (old,),
            ).fetchall()
            for row in antigos:
                duplicado = novos.get(str(row["fingerprint"]))
                if duplicado is not None and duplicado != int(row["id"]):
                    conn.execute(
                        "DELETE FROM backlog_items WHERE id = ?", (int(row["id"]),)
                    )
            cursor = conn.execute(
                "UPDATE backlog_items SET project = ? "
                "WHERE source = 'agenda' AND project = ?",
                (new, old),
            )
            return int(cursor.rowcount or 0)

    def mark_queued(self, item_id: int) -> dict[str, Any]:
        momento = _now()
        with self.store._lock, self._conn() as conn:
            conn.execute(
                "UPDATE backlog_items SET status = 'queued', queued_at = ?, "
                "updated_at = ?, attempts = attempts + 1 WHERE id = ?",
                (momento, momento, int(item_id)),
            )
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    def mark_running(self, item_id: int, *, branch: str = "", repo: str = "") -> None:
        momento = _now()
        with self.store._lock, self._conn() as conn:
            conn.execute(
                "UPDATE backlog_items SET status = 'running', started_at = ?, "
                "updated_at = ?, branch = ?, repo = ? WHERE id = ?",
                (momento, momento, branch, repo, int(item_id)),
            )

    def mark_pr(
        self,
        item_id: int,
        *,
        pr_url: str = "",
        pr_number: int = 0,
        detail: str = "",
    ) -> None:
        momento = _now()
        with self.store._lock, self._conn() as conn:
            conn.execute(
                "UPDATE backlog_items SET status = 'pr', finished_at = ?, "
                "updated_at = ?, pr_url = ?, pr_number = ?, detail = ?, error = '' "
                "WHERE id = ?",
                (momento, momento, pr_url, int(pr_number), detail[:4000], int(item_id)),
            )

    def mark_failed(self, item_id: int, error: str, *, detail: str = "") -> None:
        momento = _now()
        with self.store._lock, self._conn() as conn:
            conn.execute(
                "UPDATE backlog_items SET status = 'failed', finished_at = ?, "
                "updated_at = ?, error = ?, detail = ? WHERE id = ?",
                (momento, momento, str(error)[:4000], detail[:4000], int(item_id)),
            )

    def mark_done(self, item_id: int) -> None:
        momento = _now()
        with self.store._lock, self._conn() as conn:
            conn.execute(
                "UPDATE backlog_items SET status = 'done', finished_at = ?, "
                "updated_at = ? WHERE id = ?",
                (momento, momento, int(item_id)),
            )

    def delete(self, item_id: int) -> bool:
        with self.store._lock, self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM backlog_items WHERE id = ?", (int(item_id),)
            )
            return cursor.rowcount > 0

    def prune(self, now: datetime | None = None) -> int:
        """Remove itens de chat concluidos ha mais de N dias; devolve quantos.

        A tela so esconde (``cutoff``); esta funcao e a limpeza de verdade e
        roda no worker. Item vindo da Agenda **nao** e apagado aqui: quem manda
        no grupo e a pagina, e purgar por tempo apagaria trabalho pendente de
        uma pagina ainda viva (a ``facade`` purga so quando a pagina sumiu).
        """
        if self.prune_days <= 0:
            return 0
        momento = now or datetime.now().astimezone()
        corte = (momento - timedelta(days=self.prune_days)).isoformat(
            timespec="seconds"
        )
        with self.store._lock, self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM backlog_items WHERE source = 'chat' "
                "AND status IN ('pr','done') AND finished_at != '' "
                "AND finished_at < ?",
                (corte,),
            )
            return int(cursor.rowcount or 0)

    # ------------------------------------------------------------- agrupamento
    def pages(self, include_concluded: bool = True) -> list[dict[str, Any]]:
        """Itens agrupados por projeto (o collapsible do card).

        A ordem e: projetos com trabalho pendente primeiro, depois os que so
        tem concluidos; dentro do grupo, pendentes antes dos concluidos.
        """
        itens = self.list_items(include_concluded=include_concluded, limit=2000)
        grupos: dict[str, dict[str, Any]] = {}
        pesos = {"running": 0, "queued": 1, "failed": 2, "pending": 3, "pr": 4, "done": 5}
        for item in itens:
            projeto = str(item.get("project") or "chat")
            grupo = grupos.setdefault(
                projeto, {"project": projeto, "page_id": "", "items": []}
            )
            if not grupo["page_id"] and item.get("page_id"):
                grupo["page_id"] = str(item["page_id"])
            grupo["items"].append(item)
        lista = list(grupos.values())
        for grupo in lista:
            grupo["items"].sort(
                key=lambda i: (pesos.get(str(i.get("status")), 9), int(i.get("id") or 0))
            )
            grupo["title"] = str(grupo["items"][0].get("project") or grupo["project"])
            grupo["counts"] = {
                chave: sum(1 for i in grupo["items"] if i.get("status") == chave)
                for chave in STATUSES
            }
            grupo["open"] = bool(grupo["counts"]["pending"] + grupo["counts"]["failed"])
        lista.sort(
            key=lambda g: (
                0 if g["counts"]["pending"] + g["counts"]["failed"] else 1,
                str(g["project"]).lower(),
            )
        )
        return lista


__all__ = [
    "ACTIVE",
    "CONCLUDED",
    "STATUSES",
    "BacklogService",
    "BacklogUnavailable",
    "fingerprint",
]
