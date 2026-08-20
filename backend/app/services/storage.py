"""PostgreSQL-backed persistence for chat memory and document workspaces.

Async wrappers around synchronous PostgreSQL functions.
We use psycopg2 with asyncio.to_thread to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import os
import psycopg2
from psycopg2.extras import DictCursor
import uuid
import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.app.config import get_settings


def _db_uri() -> str:
    return get_settings().POSTGRES_URI


def _connect(db_uri: str | None = None):
    conn = psycopg2.connect(db_uri or _db_uri())
    return conn


def init_db(db_uri: str | None = None) -> None:
    uri = db_uri or _db_uri()
    with _connect(uri) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts_utc TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_documents (
                    user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    vector_index_path TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT 'default',
                    ts_utc TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, workspace_id, doc_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (workspace_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (session_id)
                )
                """
            )
            
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages ON chat_messages(user_id, workspace_id, session_id, ts_utc)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_docs ON user_documents(user_id, workspace_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_workspaces ON workspaces(user_id, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_sessions ON chat_sessions(user_id, workspace_id, updated_at)"
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Synchronous helpers (called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _load_chat_messages_sync(
    user_id: str, workspace_id: str, session_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                """
                SELECT role, content, ts_utc
                FROM chat_messages
                WHERE user_id = %s AND workspace_id = %s AND session_id = %s
                ORDER BY ts_utc DESC
                LIMIT %s
                """,
                (user_id, workspace_id, session_id, limit),
            )
            rows = cur.fetchall()
    rows = list(reversed(rows))
    # Note: ts_utc is returned as datetime object by psycopg2
    return [{"role": r["role"], "content": r["content"], "timestamp": r["ts_utc"].isoformat() if isinstance(r["ts_utc"], datetime.datetime) else r["ts_utc"]} for r in rows]


def _append_chat_messages_sync(
    user_id: str,
    workspace_id: str,
    session_id: str,
    messages: List[Dict[str, Any]],
) -> None:
    if not messages:
        return
    init_db()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (session_id, user_id, workspace_id, name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(session_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, user_id, workspace_id, "New Chat"),
            )
            cur.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id = %s", (session_id,)
            )
            count = cur.fetchone()[0]
            if count == 0:
                first_user_msg = next((m for m in messages if m.get("role") == "user"), None)
                if first_user_msg:
                    name = first_user_msg.get("content", "")[:30] + "..."
                    cur.execute(
                        "UPDATE chat_sessions SET name = %s WHERE session_id = %s",
                        (name, session_id),
                    )
            
            # psycopg2 executemany syntax
            cur.executemany(
                """
                INSERT INTO chat_messages(user_id, workspace_id, session_id, role, content, ts_utc)
                VALUES(%s, %s, %s, %s, %s, COALESCE(CAST(%s AS TIMESTAMP WITH TIME ZONE), CURRENT_TIMESTAMP))
                """,
                [
                    (
                        user_id,
                        workspace_id,
                        session_id,
                        m.get("role", "unknown"),
                        m.get("content", ""),
                        m.get("timestamp"),
                    )
                    for m in messages
                    if m.get("content")
                ],
            )
        conn.commit()


def _upsert_document_sync(
    user_id: str,
    workspace_id: str,
    session_id: str,
    doc_id: str,
    file_path: str,
    vector_index_path: str,
) -> None:
    init_db()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_documents(user_id, workspace_id, session_id, doc_id, file_path, vector_index_path)
                VALUES(%s, %s, %s, %s, %s, %s)
                ON CONFLICT(user_id, workspace_id, doc_id) DO UPDATE SET
                  session_id=EXCLUDED.session_id,
                  file_path=EXCLUDED.file_path,
                  vector_index_path=EXCLUDED.vector_index_path
                """,
                (user_id, workspace_id, session_id, doc_id, file_path, vector_index_path),
            )
        conn.commit()


def _list_workspace_documents_sync(
    user_id: str, workspace_id: str, session_id: str
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    init_db()
    with _connect() as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                """
                SELECT doc_id, file_path, vector_index_path, ts_utc
                FROM user_documents
                WHERE user_id = %s AND workspace_id = %s AND session_id = %s
                ORDER BY ts_utc ASC
                """,
                (user_id, workspace_id, session_id),
            )
            rows = cur.fetchall()
            
    docs = [
        {
            "doc_id": r["doc_id"],
            "file_path": r["file_path"],
            "vector_index_path": r["vector_index_path"],
            "timestamp": r["ts_utc"].isoformat() if isinstance(r["ts_utc"], datetime.datetime) else r["ts_utc"],
        }
        for r in rows
    ]
    index_path = docs[-1]["vector_index_path"] if docs else None
    return docs, index_path


# ---------------------------------------------------------------------------
# Async public API
# ---------------------------------------------------------------------------

async def load_chat_messages(
    user_id: str, workspace_id: str, session_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(
        _load_chat_messages_sync, user_id, workspace_id, session_id, limit
    )


async def append_chat_messages(
    user_id: str,
    workspace_id: str,
    session_id: str,
    messages: List[Dict[str, Any]],
) -> None:
    await asyncio.to_thread(
        _append_chat_messages_sync, user_id, workspace_id, session_id, messages
    )


async def upsert_document(
    user_id: str,
    workspace_id: str,
    session_id: str,
    doc_id: str,
    file_path: str,
    vector_index_path: str,
) -> None:
    await asyncio.to_thread(
        _upsert_document_sync, user_id, workspace_id, session_id, doc_id, file_path, vector_index_path
    )


async def list_workspace_documents(
    user_id: str, workspace_id: str, session_id: str
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return await asyncio.to_thread(
        _list_workspace_documents_sync, user_id, workspace_id, session_id
    )


def _list_chat_sessions_sync(user_id: str, workspace_id: str) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                """
                SELECT session_id, name, updated_at
                FROM chat_sessions
                WHERE user_id = %s AND workspace_id = %s
                ORDER BY updated_at DESC
                """,
                (user_id, workspace_id),
            )
            rows = cur.fetchall()
    return [{"session_id": r["session_id"], "name": r["name"], "updated_at": r["updated_at"].isoformat() if isinstance(r["updated_at"], datetime.datetime) else r["updated_at"]} for r in rows]


async def list_chat_sessions(user_id: str, workspace_id: str) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_list_chat_sessions_sync, user_id, workspace_id)


def _delete_chat_session_sync(user_id: str, workspace_id: str, session_id: str) -> None:
    init_db()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_messages WHERE user_id = %s AND workspace_id = %s AND session_id = %s", (user_id, workspace_id, session_id))
            cur.execute("DELETE FROM chat_sessions WHERE user_id = %s AND workspace_id = %s AND session_id = %s", (user_id, workspace_id, session_id))
        conn.commit()


async def delete_chat_session(user_id: str, workspace_id: str, session_id: str) -> None:
    await asyncio.to_thread(_delete_chat_session_sync, user_id, workspace_id, session_id)


def _rename_chat_session_sync(user_id: str, workspace_id: str, session_id: str, name: str) -> None:
    init_db()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chat_sessions SET name = %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s AND workspace_id = %s AND session_id = %s",
                (name, user_id, workspace_id, session_id)
            )
        conn.commit()


async def rename_chat_session(user_id: str, workspace_id: str, session_id: str, name: str) -> None:
    await asyncio.to_thread(_rename_chat_session_sync, user_id, workspace_id, session_id, name)


# ── Auth Operations ──────────────────────────────────────────────

async def create_user(email: str, password_hash: str, name: str = "") -> str:
    """Create a new user and return their unique user_id."""
    init_db()
    user_id = str(uuid.uuid4())
    def _create() -> None:
        with _connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO users (id, email, password_hash, name) VALUES (%s, %s, %s, %s)",
                        (user_id, email, password_hash, name)
                    )
                except psycopg2.IntegrityError:
                    conn.rollback()
                    raise ValueError("User with this email already exists")
            conn.commit()
    await asyncio.to_thread(_create)
    return user_id

async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Retrieve a user record by email."""
    init_db()
    def _get() -> Optional[Dict[str, Any]]:
        with _connect() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE email = %s", (email,))
                row = cur.fetchone()
                return dict(row) if row else None
    return await asyncio.to_thread(_get)

async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a user record by user_id."""
    init_db()
    def _get() -> Optional[Dict[str, Any]]:
        with _connect() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    return await asyncio.to_thread(_get)
