"""旅行会话持久化服务。

使用 SQLite 保存当前计划、原始请求、历史版本、Agent Trace 与 revision locks，保证服务重启后可恢复。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DATA_DIR = Path(os.getenv("TRIP_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))
DB_PATH = DATA_DIR / "trip_planner.db"


class StaleSessionError(Exception):
    """The plan changed after a revision began."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_sessions (
                session_id TEXT PRIMARY KEY,
                current_plan TEXT NOT NULL,
                request TEXT NOT NULL DEFAULT '{}',
                history TEXT NOT NULL DEFAULT '[]',
                execution_trace TEXT NOT NULL DEFAULT '[]',
                locks TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trip_sessions)").fetchall()}
        if "locks" not in columns:
            conn.execute("ALTER TABLE trip_sessions ADD COLUMN locks TEXT NOT NULL DEFAULT '{}'")
        if "request" not in columns:
            conn.execute("ALTER TABLE trip_sessions ADD COLUMN request TEXT NOT NULL DEFAULT '{}'")
        if "version" not in columns:
            conn.execute("ALTER TABLE trip_sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS trip_previews (
                preview_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                base_version INTEGER NOT NULL,
                feedback TEXT NOT NULL,
                candidate_plan TEXT NOT NULL,
                execution_trace TEXT NOT NULL,
                locks TEXT NOT NULL,
                comparison TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )"""
        )
        conn.commit()


_init_db()


def create_session(
    plan_data: Dict[str, Any],
    execution_trace: Optional[List[Dict[str, Any]]] = None,
    locks: Optional[Dict[str, Any]] = None,
    request: Optional[Dict[str, Any]] = None,
) -> str:
    """创建持久化会话并返回 session_id。"""
    session_id = str(uuid.uuid4())
    now = _utc_now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO trip_sessions (
                session_id, current_plan, request, history, execution_trace, locks, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                json.dumps(plan_data, ensure_ascii=False),
                json.dumps(request or {}, ensure_ascii=False),
                "[]",
                json.dumps(execution_trace or [], ensure_ascii=False),
                json.dumps(locks or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()

    return session_id


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """读取会话、原始请求、历史记录、锁定状态与执行轨迹。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM trip_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    if not row:
        return None

    request_payload = json.loads(row["request"] or "{}") if "request" in row.keys() else {}
    return {
        "session_id": row["session_id"],
        "current_plan": json.loads(row["current_plan"]),
        "request": request_payload,
        "history": json.loads(row["history"]),
        "execution_trace": json.loads(row["execution_trace"]),
        "locks": json.loads(row["locks"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version": row["version"],
    }


def update_session_plan(
    session_id: str,
    new_plan: Dict[str, Any],
    *,
    feedback: Optional[str] = None,
    execution_trace: Optional[List[Dict[str, Any]]] = None,
    locks: Optional[Dict[str, Any]] = None,
    expected_version: Optional[int] = None,
) -> bool:
    """更新当前计划，把旧版本写入 history，并可同步 revision locks。"""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM trip_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return False
        if expected_version is not None and row["version"] != expected_version:
            raise StaleSessionError("行程已变化，请重新生成修改方案")
        now = _utc_now()
        history = json.loads(row["history"])
        history.append({
            "updated_at": now,
            "feedback": feedback,
            "locks": json.loads(row["locks"]),
            "plan": json.loads(row["current_plan"]),
        })
        merged_trace = json.loads(row["execution_trace"])
        if execution_trace:
            merged_trace.extend(execution_trace)
        persisted_locks = json.loads(row["locks"]) if locks is None else locks
        conn.execute(
            """
            UPDATE trip_sessions
            SET current_plan = ?, history = ?, execution_trace = ?, locks = ?, updated_at = ?, version = version + 1
            WHERE session_id = ?
            """,
            (
                json.dumps(new_plan, ensure_ascii=False),
                json.dumps(history, ensure_ascii=False),
                json.dumps(merged_trace, ensure_ascii=False),
                json.dumps(persisted_locks, ensure_ascii=False),
                now,
                session_id,
            ),
        )
    return True


def update_session_locks(session_id: str, locks: Dict[str, Any]) -> bool:
    """只更新会话锁定状态，不生成新的行程历史版本。"""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE trip_sessions SET locks = ?, updated_at = ?, version = version + 1 WHERE session_id = ?",
            (json.dumps(locks, ensure_ascii=False), _utc_now(), session_id),
        )
        return cursor.rowcount > 0


def get_session_trace(session_id: str) -> Optional[List[Dict[str, Any]]]:
    session = get_session(session_id)
    return session["execution_trace"] if session else None


def create_preview(
    session: Dict[str, Any], feedback: str, candidate_plan: Dict[str, Any],
    execution_trace: List[Dict[str, Any]], locks: Dict[str, Any], comparison: Dict[str, Any],
) -> str:
    preview_id = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM trip_previews WHERE expires_at < ?", (_utc_now(),))
        conn.execute(
            """INSERT INTO trip_previews
            (preview_id, session_id, base_version, feedback, candidate_plan, execution_trace, locks, comparison, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (preview_id, session["session_id"], session["version"], feedback,
             json.dumps(candidate_plan, ensure_ascii=False),
             json.dumps(execution_trace, ensure_ascii=False),
             json.dumps(locks, ensure_ascii=False),
             json.dumps(comparison, ensure_ascii=False), expires_at),
        )
    return preview_id


def get_preview(preview_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM trip_previews WHERE preview_id = ?", (preview_id,)).fetchone()
    if row is None:
        return None
    return {
        "preview_id": row["preview_id"], "session_id": row["session_id"],
        "base_version": row["base_version"], "feedback": row["feedback"],
        "candidate_plan": json.loads(row["candidate_plan"]),
        "execution_trace": json.loads(row["execution_trace"]),
        "locks": json.loads(row["locks"]), "comparison": json.loads(row["comparison"]),
        "expires_at": row["expires_at"],
    }


def apply_preview(preview_id: str) -> str:
    """Commit a preview only if the session still matches its base version.

    Returns applied, stale, or missing. SQLite's immediate transaction makes
    the version check and history update indivisible.
    """
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        preview = conn.execute("SELECT * FROM trip_previews WHERE preview_id = ?", (preview_id,)).fetchone()
        if preview is None:
            return "missing"
        if datetime.fromisoformat(preview["expires_at"]) < datetime.now(timezone.utc):
            conn.execute("DELETE FROM trip_previews WHERE preview_id = ?", (preview_id,))
            return "missing"
        session = conn.execute("SELECT * FROM trip_sessions WHERE session_id = ?", (preview["session_id"],)).fetchone()
        if session is None:
            return "missing"
        if session["version"] != preview["base_version"]:
            return "stale"
        now = _utc_now()
        history = json.loads(session["history"])
        history.append({"updated_at": now, "feedback": preview["feedback"],
                        "locks": json.loads(session["locks"]), "plan": json.loads(session["current_plan"])})
        trace = [*json.loads(session["execution_trace"]), *json.loads(preview["execution_trace"])]
        conn.execute(
            """UPDATE trip_sessions SET current_plan = ?, history = ?, execution_trace = ?,
            locks = ?, updated_at = ?, version = version + 1 WHERE session_id = ?""",
            (preview["candidate_plan"], json.dumps(history, ensure_ascii=False),
             json.dumps(trace, ensure_ascii=False), preview["locks"], now, preview["session_id"]),
        )
        conn.execute("DELETE FROM trip_previews WHERE preview_id = ?", (preview_id,))
    return "applied"
