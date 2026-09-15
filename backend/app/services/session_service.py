"""旅行会话持久化服务。

使用 SQLite 替代进程内字典，保证服务重启后仍可恢复行程、修改历史和 Agent 执行轨迹。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DATA_DIR = Path(os.getenv("TRIP_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))
DB_PATH = DATA_DIR / "trip_planner.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_sessions (
                session_id TEXT PRIMARY KEY,
                current_plan TEXT NOT NULL,
                history TEXT NOT NULL DEFAULT '[]',
                execution_trace TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


_init_db()


def create_session(plan_data: Dict[str, Any], execution_trace: Optional[List[Dict[str, Any]]] = None) -> str:
    """创建持久化会话并返回 session_id。"""
    session_id = str(uuid.uuid4())
    now = _utc_now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO trip_sessions (
                session_id, current_plan, history, execution_trace, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                json.dumps(plan_data, ensure_ascii=False),
                "[]",
                json.dumps(execution_trace or [], ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()

    return session_id


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """读取会话、历史记录与执行轨迹。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM trip_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    if not row:
        return None

    return {
        "session_id": row["session_id"],
        "current_plan": json.loads(row["current_plan"]),
        "history": json.loads(row["history"]),
        "execution_trace": json.loads(row["execution_trace"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def update_session_plan(
    session_id: str,
    new_plan: Dict[str, Any],
    *,
    feedback: Optional[str] = None,
    execution_trace: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """更新当前计划，并把旧版本写入 history。"""
    session = get_session(session_id)
    if not session:
        return False

    history = session["history"]
    history.append(
        {
            "updated_at": _utc_now(),
            "feedback": feedback,
            "plan": session["current_plan"],
        }
    )

    merged_trace = session["execution_trace"]
    if execution_trace:
        merged_trace = [*merged_trace, *execution_trace]

    with _connect() as conn:
        conn.execute(
            """
            UPDATE trip_sessions
            SET current_plan = ?, history = ?, execution_trace = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                json.dumps(new_plan, ensure_ascii=False),
                json.dumps(history, ensure_ascii=False),
                json.dumps(merged_trace, ensure_ascii=False),
                _utc_now(),
                session_id,
            ),
        )
        conn.commit()

    return True


def get_session_trace(session_id: str) -> Optional[List[Dict[str, Any]]]:
    session = get_session(session_id)
    return session["execution_trace"] if session else None
