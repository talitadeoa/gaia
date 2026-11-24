from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from gaia.memory.store import SCHEMA_VERSION, MemoryStore


def test_conversations_and_turns(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        conv = store.get_or_create_conversation("/proj")
        assert conv["project_path"] == "/proj"
        store.set_session(int(conv["id"]), "ses_123")
        store.add_turn(int(conv["id"]), "user", "olá")
        store.add_turn(int(conv["id"]), "assistant", "oi")

        same = store.get_or_create_conversation("/proj")
        assert int(same["id"]) == int(conv["id"])
        assert same["agent_session_id"] == "ses_123"

        turns = store.turns(int(conv["id"]))
        assert [t["role"] for t in turns] == ["user", "assistant"]

        new_conv = store.create_conversation("/proj", title="nova")
        assert int(new_conv["id"]) != int(conv["id"])
        current = store.get_or_create_conversation("/proj")
        assert int(current["id"]) == int(new_conv["id"])

        store.record_note(int(new_conv["id"]), "note", "/tmp/a.md", "A")
        assert store.stats()["records"] == 1
        assert store.recent_records()[0]["title"] == "A"
    finally:
        store.close()


def test_conversations_per_mode(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assistente = store.get_or_create_conversation("/proj", "assistente")
        trabalhadora = store.get_or_create_conversation("/proj", "trabalhadora")
        assert int(assistente["id"]) != int(trabalhadora["id"])

        assert int(store.get_or_create_conversation("/proj", "assistente")["id"]) == int(
            assistente["id"]
        )
        assert int(store.get_or_create_conversation("/proj", "trabalhadora")["id"]) == int(
            trabalhadora["id"]
        )

        nova = store.create_conversation("/proj", title="nova trabalhadora", mode="trabalhadora")
        assert int(store.get_or_create_conversation("/proj", "assistente")["id"]) == int(
            assistente["id"]
        )
        assert int(store.get_or_create_conversation("/proj", "trabalhadora")["id"]) == int(
            nova["id"]
        )

        ids = {int(row["id"]) for row in store.list_conversations("/proj", "trabalhadora")}
        assert ids == {int(trabalhadora["id"]), int(nova["id"])}

        store.set_current(int(trabalhadora["id"]))
        assert int(store.get_or_create_conversation("/proj", "trabalhadora")["id"]) == int(
            trabalhadora["id"]
        )
    finally:
        store.close()


def test_migracao_v3_renomeia_modos_e_funde_conversa(tmp_path):
    path = tmp_path / "gaia.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_path TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'assistente',
            opencode_session_id TEXT,
            title TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    conn.execute("INSERT INTO meta VALUES ('mode', 'conversa')")
    antiga = "2026-11-09T09:00:00"
    recente = "2026-11-09T11:00:00"
    for mode, updated in (
        ("academica", antiga),
        ("conversa", antiga),
        ("assistente", recente),
    ):
        conn.execute(
            "INSERT INTO conversations "
            "(project_path, mode, title, is_current, created_at, updated_at) "
            "VALUES ('/proj', ?, ?, 1, ?, ?)",
            (mode, mode, updated, updated),
        )
    conn.commit()
    conn.close()

    store = MemoryStore(path)
    try:
        assert store.get_meta("mode") == "assistente"
        assert store.get_or_create_conversation("/proj", "pesquisadora")["title"] == "academica"

        atuais = store.list_conversations("/proj", "assistente")
        assert {row["title"] for row in atuais} == {"assistente", "conversa"}
        assert sum(1 for row in atuais if row["is_current"]) == 1
        assert store.get_or_create_conversation("/proj", "assistente")["title"] == "assistente"
    finally:
        store.close()


def test_rehome_conversations_e_richest_mode(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        antiga = store.get_or_create_conversation("/old/proj", "assistente")
        store.add_turn(int(antiga["id"]), "user", "oi")
        store.add_turn(int(antiga["id"]), "assistant", "olá")
        vazia = store.get_or_create_conversation("/old/proj", "investidora")
        nova = store.get_or_create_conversation("/new/proj", "assistente")

        assert store.project_has_turns("/old/proj")
        assert not store.project_has_turns("/new/proj")
        assert store.richest_mode("/old/proj") == "assistente"

        assert store.rehome_conversations("/old/proj", "/new/proj") == 2
        assert not store.project_has_turns("/old/proj")
        assert store.project_has_turns("/new/proj")

        atual = store.get_or_create_conversation("/new/proj", "assistente")
        assert int(atual["id"]) == int(antiga["id"])
        ids = {
            int(row["id"])
            for row in store.list_conversations("/new/proj", "assistente")
        }
        assert ids == {int(antiga["id"]), int(nova["id"])}
        assert (
            int(store.get_or_create_conversation("/new/proj", "investidora")["id"])
            == int(vazia["id"])
        )
    finally:
        store.close()


def test_reminders(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        rid = store.add_reminder("beber água", "2026-11-08T10:00:00+00:00")
        assert store.reminder_stats()["pending"] == 1
        assert store.due_reminders("2026-11-08T09:59:00+00:00") == []
        due = store.due_reminders("2026-11-08T10:00:00+00:00")
        assert len(due) == 1
        assert due[0]["text"] == "beber água"

        store.mark_reminder_spoken(rid)
        assert store.pending_reminders() == []
        assert store.reminder_stats()["pending"] == 0

        rid2 = store.add_reminder("estudar", "2026-11-09T09:00:00+00:00")
        assert store.cancel_reminder(rid2) is True
        assert store.cancel_reminder(rid2) is False
        assert store.pending_reminders() == []
    finally:
        store.close()


def test_meta(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store.get_meta("model") is None
        store.set_meta("model", "deepseek/deepseek-flash")
        assert store.get_meta("model") == "deepseek/deepseek-flash"
        store.set_meta("model", "deepseek/deepseek-v4-pro")
        assert store.get_meta("model") == "deepseek/deepseek-v4-pro"
    finally:
        store.close()


def test_pragmas_configured(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        store.close()


def test_concurrent_writes_are_serialized(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        conv = store.get_or_create_conversation("/proj")
        conversation_id = int(conv["id"])

        def worker(index: int) -> int:
            store.add_turn(conversation_id, "user", f"msg {index}")
            return store.stats()["turns"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(50)))

        assert store.stats()["turns"] == 50
        texts = [row["text"] for row in store.turns(conversation_id, limit=50)]
        assert len(texts) == 50
    finally:
        store.close()


def test_close_is_idempotent_and_context_manager(tmp_path):
    with MemoryStore(tmp_path / "gaia.db") as store:
        store.set_meta("k", "v")
    store.close()
    store.close()


def test_schema_version_recorded(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store.schema_version() == SCHEMA_VERSION
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
    finally:
        store.close()


def test_migrates_old_database_without_mode(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_path TEXT NOT NULL,
            opencode_session_id TEXT,
            title TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_conv_project ON conversations(project_path, is_current);
        INSERT INTO conversations (project_path, is_current, created_at, updated_at)
            VALUES ('/proj', 1, 'x', 'x');
        """
    )
    conn.commit()
    conn.close()

    with MemoryStore(db_path) as store:
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        assert "mode" in columns
        assert store.schema_version() == SCHEMA_VERSION
        assert store.current_conversation("/proj")["mode"] == "assistente"
        # o indice foi recriado cobrindo a coluna mode
        indexes = {row["name"] for row in store._conn.execute("PRAGMA index_list(conversations)")}
        assert "idx_conv_project" in indexes


def test_conversations_are_separated_by_channel(tmp_path):
    """Terminal e Telegram nao dividem conversa, mesmo com pasta e modo iguais."""
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        cli = store.get_or_create_conversation("/proj", "assistente")
        tg = store.get_or_create_conversation("/proj", "assistente", "telegram")
        assert int(cli["id"]) != int(tg["id"])
        assert cli["channel"] == "cli"
        assert tg["channel"] == "telegram"

        store.add_turn(int(tg["id"]), "user", "só no telegram")
        assert store.turns(int(cli["id"])) == []
        assert [t["text"] for t in store.turns(int(tg["id"]))] == ["só no telegram"]

        # Cada canal tem a sua "conversa atual" e a sua lista.
        assert int(store.current_conversation("/proj", "assistente")["id"]) == int(cli["id"])
        assert int(
            store.current_conversation("/proj", "assistente", "telegram")["id"]
        ) == int(tg["id"])
        assert len(store.list_conversations("/proj", "assistente")) == 1
        assert len(store.list_conversations("/proj", "assistente", "telegram")) == 1

        # Nova sessao no telegram nao mexe na atual do terminal.
        nova = store.create_conversation("/proj", title="nova", channel="telegram")
        assert int(
            store.current_conversation("/proj", "assistente", "telegram")["id"]
        ) == int(nova["id"])
        assert int(store.current_conversation("/proj", "assistente")["id"]) == int(cli["id"])

        # O modo mais rico tambem e por canal.
        store.add_turn(int(nova["id"]), "user", "oi")
        assert store.richest_mode("/proj", "telegram") == "assistente"
        assert store.richest_mode("/proj") is None
    finally:
        store.close()


def test_channel_migration_defaults_existing_conversations_to_cli(tmp_path):
    """Banco v4 (sem channel) migra com as conversas antigas no canal cli."""
    path = tmp_path / "gaia.db"
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            """
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_path TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'assistente',
                agent_session_id TEXT,
                title TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO conversations (project_path, mode, is_current, created_at, updated_at)"
            " VALUES ('/proj', 'assistente', 1, '2026-01-01', '2026-01-01')"
        )
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '4')")
    conn.close()

    store = MemoryStore(path)
    try:
        assert store.schema_version() == SCHEMA_VERSION
        atual = store.current_conversation("/proj", "assistente")
        assert atual is not None and atual["channel"] == "cli"
        tg = store.current_conversation("/proj", "assistente", "telegram")
        assert tg is None
    finally:
        store.close()


def test_schema_v6_cria_tabela_runs(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store.schema_version() >= 6
        cols = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        assert {
            "kind",
            "automation",
            "trigger",
            "status",
            "started_at",
            "finished_at",
            "duration_ms",
            "delivery",
            "error",
            "error_kind",
            "attempt",
        } <= cols
    finally:
        store.close()


def test_migracao_v5_para_v6_preserva_dados(tmp_path):
    path = tmp_path / "gaia.db"
    store = MemoryStore(path)
    store.add_reminder("beber agua", "2026-11-23T10:00:00")
    store.set_meta("schema_version", "5")
    with store._conn:
        store._conn.execute("DROP TABLE runs")
    store.close()

    store = MemoryStore(path)
    try:
        assert store.schema_version() == SCHEMA_VERSION
        assert len(store.pending_reminders()) == 1
        store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()
    finally:
        store.close()


def test_schema_v7_cria_tabela_automations(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store.schema_version() >= 7
        colunas = {
            row["name"]
            for row in store._conn.execute(
                "PRAGMA table_info(automations)"
            ).fetchall()
        }
        assert {
            "id",
            "skill",
            "enabled",
            "days",
            "at",
            "until",
            "delivery",
            "retry_max",
            "retry_backoff_minutes",
            "last_result",
            "updated_at",
        } <= colunas
    finally:
        store.close()


def test_migracao_v6_para_v7_preserva_runs(tmp_path):
    path = tmp_path / "gaia.db"
    store = MemoryStore(path)
    run = store.begin_run(kind="career", automation="career:weekly")
    store.finish_run(run, status="completed", detail="3 vagas")
    # Simula um banco da fase B: schema v6 e sem a tabela nova.
    store.set_meta("schema_version", "6")
    with store._conn:
        store._conn.execute("DROP TABLE automations")
    store.close()

    store = MemoryStore(path)
    try:
        assert store.schema_version() >= 7
        assert store.list_runs(kind="career")[0]["detail"] == "3 vagas"
        assert store.list_automations() == []
    finally:
        store.close()


def test_upsert_e_edicao_de_automacao(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        store.upsert_automation(
            id="career:weekly",
            skill="career",
            days="0",
            at="09:00",
            until="18:00",
            delivery="telegram",
        )
        item = store.get_automation("career:weekly")
        assert item is not None
        assert item["enabled"] == 1
        assert item["at"] == "09:00"

        assert store.update_automation("career:weekly", enabled=False, at="07:30")
        atual = store.get_automation("career:weekly")
        assert atual is not None
        assert atual["enabled"] == 0
        assert atual["at"] == "07:30"
        # O upsert e idempotente e nao duplica linha.
        store.upsert_automation(
            id="career:weekly", skill="career", at="08:00", until="12:00"
        )
        assert len(store.list_automations()) == 1
        assert store.update_automation("nao-existe", at="10:00") is False
    finally:
        store.close()


def test_schema_v9_tem_a_tabela_routines(tmp_path):
    """Rotinas continuas: tabela de override (linha ausente = ligada)."""
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store.schema_version() >= 9
        assert store.list_routines() == []
        # Sem linha = ligada (o registry e o padrao; a tabela so guarda excecao).
        assert store.routine_enabled("initiative:agenda") is True

        store.set_routine("initiative:agenda", enabled=False)
        assert store.routine_enabled("initiative:agenda") is False
        linha = store.get_routine("initiative:agenda")
        assert linha is not None and linha["enabled"] == 0

        # Idempotente: pausar duas vezes nao duplica nem re-cria.
        store.set_routine("initiative:agenda", enabled=False)
        assert len(store.list_routines()) == 1

        store.set_routine("initiative:agenda", enabled=True)
        assert store.routine_enabled("initiative:agenda") is True
    finally:
        store.close()
