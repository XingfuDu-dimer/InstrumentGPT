"""SQLite database for per-IP conversation history."""
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent / "data" / "conversations.db"


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                ip_address TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New Chat',
                cli_session_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_ip
                ON conversations(ip_address, updated_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv
                ON messages(conversation_id, created_at ASC);
        """)
        # Migration: add memory columns if they don't exist
        for col, default in (("summary", "''"), ("diagnostic_state", "''")):
            try:
                conn.execute(
                    f"ALTER TABLE conversations ADD COLUMN {col} TEXT DEFAULT {default}"
                )
            except sqlite3.OperationalError:
                pass  # column already exists

        # Liked entries (knowledge base) — one per (conversation, answer)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS liked_entries (
                conversation_id TEXT NOT NULL,
                last_message_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'summarizing', 'completed', 'cancelled')),
                file_path TEXT,
                worker_pid INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (conversation_id, last_message_id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_liked_status ON liked_entries(status);
            CREATE INDEX IF NOT EXISTS idx_liked_conv ON liked_entries(conversation_id);
        """)
        # Migration: if old schema (conversation_id only PK), migrate to new
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='liked_entries'"
            ).fetchone()
            old_sql = row[0] if row else ""
            if old_sql and "last_message_id" not in old_sql:
                conn.executescript("""
                    CREATE TABLE liked_entries_new (
                        conversation_id TEXT NOT NULL,
                        last_message_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        file_path TEXT,
                        worker_pid INTEGER,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (conversation_id, last_message_id)
                    );
                    INSERT INTO liked_entries_new
                    SELECT le.conversation_id,
                        COALESCE((SELECT MAX(m.id) FROM messages m WHERE m.conversation_id = le.conversation_id), 0),
                        le.status, le.file_path, le.worker_pid, le.created_at, le.updated_at
                    FROM liked_entries le;
                    DROP TABLE liked_entries;
                    ALTER TABLE liked_entries_new RENAME TO liked_entries;
                    CREATE INDEX IF NOT EXISTS idx_liked_status ON liked_entries(status);
                    CREATE INDEX IF NOT EXISTS idx_liked_conv ON liked_entries(conversation_id);
                """)
        except sqlite3.OperationalError:
            pass  # migration already done


def create_conversation(ip_address: str, title: str = "New Chat") -> str:
    conv_id = uuid.uuid4().hex[:12]
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations "
            "(id, ip_address, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conv_id, ip_address, title, now, now),
        )
    return conv_id


def get_conversations(ip_address: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at "
            "FROM conversations WHERE ip_address = ? "
            "ORDER BY updated_at DESC",
            (ip_address,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, ip_address, title, cli_session_id, "
            "created_at, updated_at "
            "FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def get_messages(conversation_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, created_at "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages_up_to(conversation_id: str, last_message_id: int) -> list[dict]:
    """Get messages from start up to and including last_message_id."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, created_at "
            "FROM messages WHERE conversation_id = ? AND id <= ? "
            "ORDER BY created_at ASC",
            (conversation_id, last_message_id),
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(conversation_id: str, role: str, content: str):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )


def update_title(conversation_id: str, title: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, conversation_id),
        )


def update_cli_session(conversation_id: str, cli_session_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET cli_session_id = ? WHERE id = ?",
            (cli_session_id, conversation_id),
        )


def get_memory(conversation_id: str) -> tuple[str, str]:
    """Return (summary, diagnostic_state_json) for a conversation."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT summary, diagnostic_state "
            "FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    if not row:
        return "", ""
    return row["summary"] or "", row["diagnostic_state"] or ""


def update_memory(conversation_id: str, summary: str, diagnostic_state: str):
    """Persist updated summary and diagnostic state."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET summary = ?, diagnostic_state = ? WHERE id = ?",
            (summary, diagnostic_state, conversation_id),
        )


def delete_conversation(conversation_id: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM liked_entries WHERE conversation_id = ?",
            (conversation_id,),
        )


# ── Liked entries (knowledge base) ────────────────────────────────────────────


def get_liked_entry(conversation_id: str, last_message_id: int | None = None) -> dict | None:
    """Return liked entry for (conv, message). If last_message_id is None, matches any (legacy)."""
    with get_conn() as conn:
        if last_message_id is not None:
            row = conn.execute(
                "SELECT conversation_id, last_message_id, status, file_path, worker_pid, created_at, updated_at "
                "FROM liked_entries WHERE conversation_id = ? AND last_message_id = ?",
                (conversation_id, last_message_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT conversation_id, last_message_id, status, file_path, worker_pid, created_at, updated_at "
                "FROM liked_entries WHERE conversation_id = ? LIMIT 1",
                (conversation_id,),
            ).fetchone()
    return dict(row) if row else None


def get_liked_entries_for_conversation(conversation_id: str) -> dict[int, dict]:
    """Return {last_message_id: {status, file_path, ...}} for all liked answers in this conv."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT conversation_id, last_message_id, status, file_path, worker_pid, created_at, updated_at "
            "FROM liked_entries WHERE conversation_id = ? AND status IN ('pending', 'summarizing', 'completed')",
            (conversation_id,),
        ).fetchall()
    return {r["last_message_id"]: dict(r) for r in rows}


def get_liked_conversation_ids(ip_address: str) -> set[str]:
    """Return set of conversation IDs that are liked (status=completed) for this IP."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT le.conversation_id FROM liked_entries le "
            "JOIN conversations c ON c.id = le.conversation_id "
            "WHERE c.ip_address = ? AND le.status = 'completed'",
            (ip_address,),
        ).fetchall()
    return {r["conversation_id"] for r in rows}


def get_liked_entries_for_ip(ip_address: str) -> dict[str, list[dict]]:
    """Return {conv_id: [entry, ...]} for all liked entries of this IP."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT le.conversation_id, le.last_message_id, le.status, le.file_path, le.worker_pid "
            "FROM liked_entries le "
            "JOIN conversations c ON c.id = le.conversation_id "
            "WHERE c.ip_address = ? AND le.status IN ('pending', 'summarizing', 'completed')",
            (ip_address,),
        ).fetchall()
    result: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        cid = d["conversation_id"]
        if cid not in result:
            result[cid] = []
        result[cid].append(d)
    return result


def create_liked_entry(
    conversation_id: str, last_message_id: int, worker_pid: int | None = None
) -> None:
    """Create a liked entry. Status is 'summarizing' if pid given, else 'pending'."""
    now = time.time()
    status = "summarizing" if worker_pid else "pending"
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO liked_entries "
            "(conversation_id, last_message_id, status, file_path, worker_pid, created_at, updated_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?)",
            (conversation_id, last_message_id, status, worker_pid, now, now),
        )


def update_liked_status(
    conversation_id: str,
    last_message_id: int,
    status: str,
    file_path: str | None = None,
) -> None:
    """Update liked entry status. Set file_path when status='completed'."""
    now = time.time()
    with get_conn() as conn:
        if file_path is not None:
            conn.execute(
                "UPDATE liked_entries SET status = ?, file_path = ?, worker_pid = NULL, updated_at = ? "
                "WHERE conversation_id = ? AND last_message_id = ?",
                (status, file_path, now, conversation_id, last_message_id),
            )
        else:
            conn.execute(
                "UPDATE liked_entries SET status = ?, worker_pid = NULL, updated_at = ? "
                "WHERE conversation_id = ? AND last_message_id = ?",
                (status, now, conversation_id, last_message_id),
            )


def delete_liked_entry(conversation_id: str, last_message_id: int) -> None:
    """Remove liked entry (e.g. on Unlike)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM liked_entries WHERE conversation_id = ? AND last_message_id = ?",
            (conversation_id, last_message_id),
        )
