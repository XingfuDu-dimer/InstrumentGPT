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
            "SELECT role, content, created_at "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at ASC",
            (conversation_id,),
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
