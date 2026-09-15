# backend/app/services/session_service.py

import uuid
from typing import Dict, Any, Optional

# 使用内存字典模拟数据库
# 结构: { "session_id": { "current_plan": {...}, "history": [] } }
SESSIONS: Dict[str, Any] = {}

def create_session(plan_data: Dict[str, Any]) -> str:
    """创建新会话，保存初始计划，返回 session_id"""
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {
        "current_plan": plan_data,
        "history": [] 
    }
    return session_id

def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """获取会话数据"""
    return SESSIONS.get(session_id)

def update_session_plan(session_id: str, new_plan: Dict[str, Any]):
    """更新会话中的计划"""
    if session_id in SESSIONS:
        SESSIONS[session_id]["current_plan"] = new_plan