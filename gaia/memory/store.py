"""Memoria operacional da Gaia: conversas, turnos e registros (SQLite).

As consultas sao locais e de microssegundos, entao o acesso continua sincrono
(chamar ``asyncio.to_thread`` para cada SELECT custaria mais do que a query).
Para tornar seguro o ``check_same_thread=False``, todas as operacoes -- leitura
e escrita -- passam pelo mesmo ``RLock``, e o banco roda em WAL com
``busy_timeout`` para tolerar acesso concorrente de outras threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

# Versao atual do schema. Incremente ao adicionar novas migracoes em _migrate.
SCHEMA_VERSION = 12

# v5: conversas separadas por canal (cli, telegram, whatsapp).
DEFAULT_CHANNEL = "cli"

# v3: modos renomeados e "conversa" fundida em "assistente".
MODE_KEY_RENAMES = {
    "academica": "pesquisadora",
    "carreira": "trabalhadora",
    "financas": "investidora",
    "conversa": "assistente",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, db_path: Path):
        db_path = Path(db_path).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_path TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'assistente',
                    channel TEXT NOT NULL DEFAULT 'cli',
                    agent_session_id TEXT,
                    title TEXT,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                );

                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    kind TEXT NOT NULL,
                    path TEXT,
                    title TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    spoken INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(spoken, cancelled, due_at);

                -- v6: historico de execucao das rotinas/skills (Control Center).
                -- O agendador continua sendo o dos workers; esta tabela so
                -- registra o que de fato executou ("iniciou" nao e sucesso).
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    automation TEXT NOT NULL DEFAULT '',
                    trigger TEXT NOT NULL DEFAULT 'schedule',
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    delivery TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    error_kind TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_runs_started
                    ON runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_kind
                    ON runs(kind, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_status
                    ON runs(status, started_at DESC);

                -- v7: agenda governavel (Control Center fase C). A tabela e a
                -- fonte de verdade de QUANDO cada rotina roda; as colunas
                -- espelham ``gaia/control/automations.py``. O seed inicial vem
                -- do Config e preserva o comportamento anterior.
                CREATE TABLE IF NOT EXISTS automations (
                    id TEXT PRIMARY KEY,
                    skill TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    days TEXT NOT NULL DEFAULT '',
                    at TEXT NOT NULL DEFAULT '',
                    until TEXT NOT NULL DEFAULT '',
                    delivery TEXT NOT NULL DEFAULT 'local',
                    retry_max INTEGER NOT NULL DEFAULT 2,
                    retry_backoff_minutes REAL NOT NULL DEFAULT 10,
                    last_result TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    -- v11: automacoes criadas pelo chat (id `user:*`) precisam
                    -- de nome proprio e dos parametros do handler; para as do
                    -- registry os dois ficam vazios (o config manda).
                    name TEXT NOT NULL DEFAULT '',
                    params TEXT NOT NULL DEFAULT ''
                );

                -- v8: backlog do painel (paginas "backlog" da Agenda + o que
                -- chega pelo chat/voz). O item guarda o estado do despacho ao
                -- pi; a limpeza de 7 dias e por filtro de finished_at, entao o
                -- historico continua aqui.
                CREATE TABLE IF NOT EXISTS backlog_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL DEFAULT 'agenda',
                    page_id TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    title_html TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    source_done INTEGER NOT NULL DEFAULT 0,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    repo TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT '',
                    pr_url TEXT NOT NULL DEFAULT '',
                    pr_number INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    queued_at TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_backlog_status
                    ON backlog_items(status, id);
                CREATE INDEX IF NOT EXISTS idx_backlog_page
                    ON backlog_items(source, project, fingerprint);
                CREATE INDEX IF NOT EXISTS idx_backlog_finished
                    ON backlog_items(status, finished_at);

                -- v9: rotinas continuas (boot/laco/evento/varredura). Diferente
                -- de `automations`, aqui NAO ha agenda: a tabela e so o override
                -- da decisao da pessoa (`enabled`). Linha ausente = ligada, entao
                -- nao existe seed nem sincronizacao com o registry.
                CREATE TABLE IF NOT EXISTS routines (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                -- v12: cada bot tem feed proprio no painel (resultado de
                -- segundo plano e recado entre bots). `seen` alimenta o badge
                -- de nao-lido; nao existe "lido por dispositivo".
                CREATE TABLE IF NOT EXISTS bot_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'job',   -- job | inter_bot | aviso
                    title TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    seen INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bot_messages_bot
                    ON bot_messages(bot, seen, created_at DESC);
                """
            )
            self._migrate()

    def _migrate(self) -> None:
        """Aplica migracoes pendentes e registra a versao do schema."""
        version = self.schema_version()
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        # v2: conversas passaram a ser separadas por modo.
        if "mode" not in columns:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'assistente'"
                )
        # v5: conversas passaram a ser separadas tambem por canal de origem,
        # para que terminal e Telegram nao dividam o mesmo historico.
        if "channel" not in columns:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE conversations ADD COLUMN channel TEXT NOT NULL DEFAULT 'cli'"
                )
        with self._conn:
            if version < SCHEMA_VERSION:
                # remove o indice antigo (project_path, is_current) e recria
                # com as colunas mode/channel, que podem ter acabado de ser
                # adicionadas.
                self._conn.execute("DROP INDEX IF EXISTS idx_conv_project")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_project "
                "ON conversations(project_path, mode, channel, is_current)"
            )
        if version < 3:
            self._rename_modes_v3()
        if version < 4:
            self._rename_session_column_v4()
        if version < 10:
            # v10: a identidade do item do backlog e (project, fingerprint) -- o
            # `project` sai do titulo da pagina e sobrevive ao rename no
            # Supabase. O indice antigo era por `page_id`.
            with self._conn:
                self._conn.execute("DROP INDEX IF EXISTS idx_backlog_page")
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_backlog_page "
                    "ON backlog_items(source, project, fingerprint)"
                )
        if version < 11:
            # v11: automacoes dinamicas (`user:*`) guardam nome e parametros.
            # `params` e JSON em texto: handler e painel leem o mesmo campo.
            existentes = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(automations)"
                ).fetchall()
            }
            with self._conn:
                if "name" not in existentes:
                    self._conn.execute(
                        "ALTER TABLE automations ADD COLUMN name TEXT NOT NULL DEFAULT ''"
                    )
                if "params" not in existentes:
                    self._conn.execute(
                        "ALTER TABLE automations ADD COLUMN params TEXT NOT NULL DEFAULT ''"
                    )
        if version < 12:
            # v12: atribuicao de bot na agenda e no historico de execucao. A
            # coluna vazia = `gaia` (comportamento de antes dos bots).
            with self._conn:
                for tabela in ("runs", "automations"):
                    colunas = {
                        row["name"]
                        for row in self._conn.execute(
                            f"PRAGMA table_info({tabela})"
                        ).fetchall()
                    }
                    if "bot" not in colunas:
                        self._conn.execute(
                            f"ALTER TABLE {tabela} ADD COLUMN bot TEXT NOT NULL DEFAULT ''"
                        )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runs_bot "
                    "ON runs(bot, started_at DESC)"
                )
        if version < SCHEMA_VERSION:
            self.set_meta("schema_version", str(SCHEMA_VERSION))

    def _rename_modes_v3(self) -> None:
        """Renomeia os modos e funde as conversas de 'conversa' em 'assistente'."""
        with self._conn:
            for old, new in MODE_KEY_RENAMES.items():
                self._conn.execute("UPDATE conversations SET mode = ? WHERE mode = ?", (new, old))
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'mode'").fetchone()
            if row and row["value"] in MODE_KEY_RENAMES:
                self._conn.execute(
                    "UPDATE meta SET value = ? WHERE key = 'mode'",
                    (MODE_KEY_RENAMES[row["value"]],),
                )
            # A fusao pode deixar mais de uma conversa "atual" no mesmo
            # projeto+modo; mantem como atual apenas a mais recente.
            self._conn.execute(
                """
                UPDATE conversations
                SET is_current = 0
                WHERE is_current = 1
                  AND id NOT IN (
                      SELECT id FROM conversations AS newer
                      WHERE newer.project_path = conversations.project_path
                        AND newer.mode = conversations.mode
                        AND newer.channel = conversations.channel
                      ORDER BY newer.updated_at DESC, newer.id DESC
                      LIMIT 1
                  )
                """
            )

    def _rename_session_column_v4(self) -> None:
        """A sessao deixou de ser do OpenCode e passou a ser do agente proprio."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "agent_session_id" in columns or "opencode_session_id" not in columns:
            return
        with self._conn:
            self._conn.execute(
                "ALTER TABLE conversations RENAME COLUMN opencode_session_id TO agent_session_id"
            )

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        try:
            return int(row["value"]) if row else 1
        except (TypeError, ValueError):
            return 1

    # ------------------------------------------------------------ conversations
    def current_conversation(
        self,
        project_path: str,
        mode: str = "assistente",
        channel: str = DEFAULT_CHANNEL,
    ) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM conversations
                WHERE project_path = ? AND mode = ? AND channel = ? AND is_current = 1
                ORDER BY id DESC LIMIT 1
                """,
                (project_path, mode, channel),
            ).fetchone()

    def create_conversation(
        self,
        project_path: str,
        title: str | None = None,
        mode: str = "assistente",
        channel: str = DEFAULT_CHANNEL,
    ) -> sqlite3.Row:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET is_current = 0 "
                "WHERE project_path = ? AND mode = ? AND channel = ?",
                (project_path, mode, channel),
            )
            cursor = self._conn.execute(
                """
                INSERT INTO conversations
                    (project_path, mode, channel, title, is_current, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (project_path, mode, channel, title or "", now, now),
            )
            conv_id = cursor.lastrowid
        if conv_id is None:
            raise RuntimeError("nao foi possivel criar a conversa")
        return self.get_conversation(conv_id)

    def get_conversation(self, conversation_id: int) -> sqlite3.Row:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"conversa {conversation_id} nao encontrada")
        return row

    def get_or_create_conversation(
        self,
        project_path: str,
        mode: str = "assistente",
        channel: str = DEFAULT_CHANNEL,
    ) -> sqlite3.Row:
        row = self.current_conversation(project_path, mode, channel)
        if row:
            return row
        return self.create_conversation(project_path, mode=mode, channel=channel)

    def list_conversations(
        self,
        project_path: str,
        mode: str,
        channel: str = DEFAULT_CHANNEL,
    ) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._conn.execute(
                    """
                    SELECT c.*,
                           (SELECT COUNT(*) FROM turns t
                            WHERE t.conversation_id = c.id) AS turn_count
                    FROM conversations c
                    WHERE c.project_path = ? AND c.mode = ? AND c.channel = ?
                    ORDER BY c.updated_at DESC, c.id DESC
                    """,
                    (project_path, mode, channel),
                ).fetchall()
            )

    def project_has_turns(self, project_path: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM turns t
                JOIN conversations c ON c.id = t.conversation_id
                WHERE c.project_path = ?
                LIMIT 1
                """,
                (project_path,),
            ).fetchone()
        return row is not None

    def richest_mode(
        self, project_path: str, channel: str = DEFAULT_CHANNEL
    ) -> str | None:
        """Modo com mais turnos no projeto (útil para retomar o histórico)."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT c.mode AS mode, COUNT(t.id) AS total
                FROM conversations c
                LEFT JOIN turns t ON t.conversation_id = c.id
                WHERE c.project_path = ? AND c.channel = ?
                GROUP BY c.mode
                ORDER BY total DESC, c.updated_at DESC
                LIMIT 1
                """,
                (project_path, channel),
            ).fetchone()
        if row is None or int(row["total"]) == 0:
            return None
        return str(row["mode"])

    def rehome_conversations(self, old_path: str, new_path: str) -> int:
        """Move conversas de um projeto antigo para o atual e devolve quantas."""
        if old_path == new_path:
            return 0
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, mode, channel, updated_at FROM conversations "
                "WHERE project_path = ?",
                (old_path,),
            ).fetchall()
            if not rows:
                return 0
            latest: dict[tuple[str, str], tuple[str, int]] = {}
            for row in rows:
                key = (str(row["mode"]), str(row["channel"]))
                updated = str(row["updated_at"])
                if key not in latest or updated > latest[key][0]:
                    latest[key] = (updated, int(row["id"]))
            self._conn.execute(
                "UPDATE conversations SET project_path = ? WHERE project_path = ?",
                (new_path, old_path),
            )
            # A conversa mais recente de cada modo/canal volta a ser a atual.
            for (mode, channel), (_, conversation_id) in latest.items():
                self._conn.execute(
                    "UPDATE conversations SET is_current = 0 "
                    "WHERE project_path = ? AND mode = ? AND channel = ?",
                    (new_path, mode, channel),
                )
                self._conn.execute(
                    "UPDATE conversations SET is_current = 1 WHERE id = ?",
                    (conversation_id,),
                )
        return len(rows)

    def set_current(self, conversation_id: int) -> None:
        conv = self.get_conversation(conversation_id)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET is_current = 0 "
                "WHERE project_path = ? AND mode = ? AND channel = ?",
                (conv["project_path"], conv["mode"], conv["channel"]),
            )
            self._conn.execute(
                "UPDATE conversations SET is_current = 1, updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )

    def set_session(self, conversation_id: int, session_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET agent_session_id = ?, updated_at = ? WHERE id = ?",
                (session_id, _now(), conversation_id),
            )

    def touch(self, conversation_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )

    # -------------------------------------------------------------------- turns
    def add_turn(self, conversation_id: int, role: str, text: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO turns (conversation_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, text, _now()),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )

    def turns(self, conversation_id: int, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM turns WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def turns_between(
        self, start_iso: str, end_iso: str, project_path: str | None = None
    ) -> list[sqlite3.Row]:
        """Turnos de todas as conversas num intervalo (para consolidar o dia)."""
        query = """
            SELECT t.*, c.mode AS mode, c.title AS conversation_title,
                   c.project_path AS project_path
            FROM turns t
            JOIN conversations c ON c.id = t.conversation_id
            WHERE t.created_at >= ? AND t.created_at < ?
        """
        params: list[Any] = [start_iso, end_iso]
        if project_path:
            query += " AND c.project_path = ?"
            params.append(project_path)
        query += " ORDER BY t.id ASC"
        with self._lock:
            return list(self._conn.execute(query, params).fetchall())

    # ------------------------------------------------------------------ records
    def record_note(
        self,
        conversation_id: int | None,
        kind: str,
        path: str,
        title: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO records (conversation_id, kind, path, title, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, kind, path, title, _now()),
            )

    def records_between(self, start_iso: str, end_iso: str) -> list[sqlite3.Row]:
        """Registros (notas criadas etc.) num intervalo de tempo."""
        with self._lock:
            return list(
                self._conn.execute(
                    "SELECT * FROM records WHERE created_at >= ? AND created_at < ? "
                    "ORDER BY id ASC",
                    (start_iso, end_iso),
                ).fetchall()
            )

    def recent_records(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM records ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    # ----------------------------------------------------------------- reminders
    def add_reminder(self, text: str, due_at: str) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO reminders (text, due_at, created_at)
                VALUES (?, ?, ?)
                """,
                (text, due_at, _now()),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("nao foi possivel criar o lembrete")
            return int(cursor.lastrowid)

    def due_reminders(self, now_iso: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM reminders
                WHERE spoken = 0 AND cancelled = 0 AND due_at <= ?
                ORDER BY due_at ASC
                """,
                (now_iso,),
            ).fetchall()

    def pending_reminders(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM reminders
                WHERE spoken = 0 AND cancelled = 0
                ORDER BY due_at ASC
                """
            ).fetchall()

    def mark_reminder_spoken(self, reminder_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE reminders SET spoken = 1 WHERE id = ?", (reminder_id,))

    def cancel_reminder(self, reminder_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE reminders SET cancelled = 1 WHERE id = ? AND cancelled = 0",
                (reminder_id,),
            )
            return cursor.rowcount > 0

    def reminder_stats(self) -> dict[str, int]:
        with self._lock:
            pending = self._conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE spoken = 0 AND cancelled = 0"
            ).fetchone()[0]
        return {"pending": int(pending)}

    # --------------------------------------------------------------------- meta
    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def claim_meta(self, key: str, value: str) -> bool:
        """Reserva uma chave de forma atomica; False se ja existia."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )
            return cursor.rowcount > 0

    def delete_meta(self, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM meta WHERE key = ?", (key,))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def count_meta(self, prefix: str) -> int:
        """Quantas chaves de ``meta`` comecam com o prefixo.

        Contadores por dia (ex.: ``automation_created:2026-11-23:``) usam
        chaves independentes em vez de um numero: assim a mesma criacao
        reenviada nao conta duas vezes e um crash no meio nao perde a conta.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM meta WHERE substr(key, 1, ?) = ?",
                (len(prefix), prefix),
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ runs
    def begin_run(
        self,
        *,
        kind: str,
        automation: str = "",
        trigger: str = "schedule",
        delivery: str = "",
        attempt: int = 1,
        started_at: str = "",
        bot: str = "",
    ) -> int:
        """Abre uma execucao e devolve o id. Chamar so quando de fato executa.

        ``started_at`` permite registrar uma execucao ja ocorrida (importacao
        de historico); vazio usa o instante atual. ``bot`` e a atribuicao da
        rotina (vazio = `gaia`, o comportamento de antes dos bots).
        """
        started = started_at or _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO runs
                    (kind, automation, trigger, status, started_at,
                     delivery, attempt, bot)
                VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (kind, automation, trigger, started, delivery, int(attempt), bot),
            )
            return int(cursor.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        detail: str = "",
        error: str = "",
        error_kind: str = "",
    ) -> None:
        """Fecha a execucao, calculando a duracao pelo started_at gravado."""
        if not run_id:
            return
        finished = _now()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT started_at FROM runs WHERE id = ?", (int(run_id),)
            ).fetchone()
            duration: int | None = None
            if row is not None:
                try:
                    inicio = datetime.fromisoformat(str(row["started_at"]))
                    duration = max(
                        0, int((datetime.now().astimezone() - inicio).total_seconds() * 1000)
                    )
                except (TypeError, ValueError):
                    duration = None
            self._conn.execute(
                """
                UPDATE runs
                SET status = ?, finished_at = ?, duration_ms = ?,
                    detail = ?, error = ?, error_kind = ?
                WHERE id = ?
                """,
                (
                    status,
                    finished,
                    duration,
                    detail[:4000],
                    error[:4000],
                    error_kind,
                    int(run_id),
                ),
            )

    def list_runs(
        self, limit: int = 50, kind: str = "", status: str = "", bot: str = ""
    ) -> list[dict[str, Any]]:
        """Execucoes mais recentes primeiro, com filtro opcional."""
        clausulas: list[str] = []
        valores: list[Any] = []
        if kind:
            clausulas.append("kind = ?")
            valores.append(kind)
        if status:
            clausulas.append("status = ?")
            valores.append(status)
        if bot:
            clausulas.append("bot = ?")
            valores.append(bot)
        where = f"WHERE {' AND '.join(clausulas)}" if clausulas else ""
        valores.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM runs {where} ORDER BY id DESC LIMIT ?",
                valores,
            ).fetchall()
        return [dict(row) for row in rows]

    def last_run(self, kind: str, automation: str = "") -> dict[str, Any] | None:
        with self._lock:
            if automation:
                row = self._conn.execute(
                    "SELECT * FROM runs WHERE kind = ? AND automation = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (kind, automation),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM runs WHERE kind = ? ORDER BY id DESC LIMIT 1",
                    (kind,),
                ).fetchone()
        return dict(row) if row else None

    def runs_summary(self, since_iso: str) -> dict[str, Any]:
        """Contagem por status e por skill desde um instante ISO."""
        with self._lock:
            por_status = self._conn.execute(
                "SELECT status, COUNT(*) AS total FROM runs "
                "WHERE started_at >= ? GROUP BY status",
                (since_iso,),
            ).fetchall()
            por_kind = self._conn.execute(
                "SELECT kind, COUNT(*) AS total, "
                "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS falhas "
                "FROM runs WHERE started_at >= ? GROUP BY kind",
                (since_iso,),
            ).fetchall()
            total = self._conn.execute(
                "SELECT COUNT(*) FROM runs WHERE started_at >= ?", (since_iso,)
            ).fetchone()[0]
        return {
            "since": since_iso,
            "total": int(total),
            "by_status": {row["status"]: int(row["total"]) for row in por_status},
            "by_kind": {
                row["kind"]: {
                    "total": int(row["total"]),
                    "failed": int(row["falhas"] or 0),
                }
                for row in por_kind
            },
        }

    def prune_runs(self, before_iso: str) -> int:
        """Remove execucoes antigas; devolve quantas sairam."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM runs WHERE started_at < ?", (before_iso,)
            )
            return int(cursor.rowcount or 0)

    # ------------------------------------------------------------- automations
    def list_automations(self) -> list[dict[str, Any]]:
        """Agenda persistida, na ordem estavel de ``id``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM automations ORDER BY id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_automation(self, automation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM automations WHERE id = ?", (automation_id,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_automation(
        self,
        *,
        id: str,
        skill: str,
        enabled: bool = True,
        days: str = "",
        at: str = "",
        until: str = "",
        delivery: str = "local",
        retry_max: int = 2,
        retry_backoff_minutes: float = 10.0,
        last_result: str = "",
        name: str = "",
        params: dict[str, Any] | None = None,
        bot: str = "",
    ) -> None:
        """Cria ou substitui uma automacao (todas as colunas).

        ``days`` e ``at``/``until`` ficam como texto (``"0,2,4"`` e
        ``"HH:MM"``) para a tabela nao depender de tipo do SQLite. ``params``
        e o JSON do handler (ex.: ``{"text": "estudar"}`` do lembrete); vai
        como texto para o painel e o agendador lerem o mesmo campo.
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO automations
                    (id, skill, enabled, days, at, until, delivery,
                     retry_max, retry_backoff_minutes, last_result, name,
                     params, bot, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    skill = excluded.skill,
                    enabled = excluded.enabled,
                    days = excluded.days,
                    at = excluded.at,
                    until = excluded.until,
                    delivery = excluded.delivery,
                    retry_max = excluded.retry_max,
                    retry_backoff_minutes = excluded.retry_backoff_minutes,
                    last_result = excluded.last_result,
                    name = excluded.name,
                    params = excluded.params,
                    bot = excluded.bot,
                    updated_at = excluded.updated_at
                """,
                (
                    id,
                    skill,
                    int(bool(enabled)),
                    days,
                    at,
                    until,
                    delivery,
                    int(retry_max),
                    float(retry_backoff_minutes),
                    last_result,
                    name,
                    json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
                    bot,
                    _now(),
                ),
            )

    def update_automation(self, automation_id: str, **campos: Any) -> bool:
        """Edicao parcial (so as colunas enviadas); False se nao existe."""
        permitidas = {
            "skill",
            "enabled",
            "days",
            "at",
            "until",
            "delivery",
            "retry_max",
            "retry_backoff_minutes",
            "last_result",
            "name",
            "params",
            "bot",
        }
        mudancas = {chave: valor for chave, valor in campos.items() if chave in permitidas}
        if not mudancas:
            return self.get_automation(automation_id) is not None
        if "enabled" in mudancas:
            mudancas["enabled"] = int(bool(mudancas["enabled"]))
        if "params" in mudancas and not isinstance(mudancas["params"], str):
            mudancas["params"] = json.dumps(
                mudancas["params"] or {}, ensure_ascii=False, sort_keys=True
            )
        colunas = ", ".join(f"{chave} = ?" for chave in mudancas)
        valores = [*mudancas.values(), _now(), automation_id]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"UPDATE automations SET {colunas}, updated_at = ? WHERE id = ?",
                valores,
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------ bot_messages
    def add_bot_message(
        self, bot: str, kind: str = "job", title: str = "", text: str = ""
    ) -> int:
        """Grava um item do feed do bot (resultado de job ou recado entre bots)."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO bot_messages (bot, kind, title, text, seen, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (bot, kind, title, text, _now()),
            )
            return int(cursor.lastrowid or 0)

    def list_bot_messages(
        self, bot: str, limit: int = 50, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        """Feed do bot, mais novo primeiro."""
        sql = "SELECT * FROM bot_messages WHERE bot = ?"
        valores: list[Any] = [bot]
        if unread_only:
            sql += " AND seen = 0"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        valores.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(sql, valores).fetchall()
        return [dict(row) for row in rows]

    def mark_bot_seen(self, bot: str) -> int:
        """Marca tudo como lido e devolve quantos itens mudaram."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE bot_messages SET seen = 1 WHERE bot = ? AND seen = 0",
                (bot,),
            )
            return cursor.rowcount

    def unread_by_bot(self) -> dict[str, int]:
        """Contagem de nao-lidos por bot (o badge do rail)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT bot, COUNT(*) AS total FROM bot_messages "
                "WHERE seen = 0 GROUP BY bot"
            ).fetchall()
        return {row["bot"]: int(row["total"]) for row in rows}

    # ---------------------------------------------------------------- routines
    def list_routines(self) -> list[dict[str, Any]]:
        """Overrides de rotina continua, em ordem estavel de ``id``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routines ORDER BY id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_routine(self, routine_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM routines WHERE id = ?", (routine_id,)
            ).fetchone()
        return dict(row) if row else None

    def routine_enabled(self, routine_id: str) -> bool:
        """Ligada? Sem linha o padrao e ligada (a tabela so guarda excecao)."""
        linha = self.get_routine(routine_id)
        return True if linha is None else bool(linha["enabled"])

    def set_routine(self, routine_id: str, *, enabled: bool) -> None:
        """Grava a decisao da pessoa (upsert: uma linha por rotina)."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO routines (id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (routine_id, int(bool(enabled)), _now()),
            )

    def backlog_ready(self) -> bool:
        """A tabela do backlog existe? (banco migrado para o schema v8)"""
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'backlog_items'"
            ).fetchone()
        return row is not None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conversations = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            turns = self._conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            records = self._conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {
            "conversations": int(conversations),
            "turns": int(turns),
            "records": int(records),
        }
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
