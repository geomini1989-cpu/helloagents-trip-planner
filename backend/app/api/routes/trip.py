"""旅行规划 API 路由。"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...agents.trip_planner_agent import get_trip_planner_agent
from ...models.schemas import RevisionLocks, TripRequest
from ...services.constraint_service import merge_constraints_from_text
from ...services.orchestration_service import execute_trip_plan
from ...services.revision_lock_service import enforce_revision_locks, merge_revision_locks
from ...services.session_service import (
    create_session,
    get_session,
    get_session_trace,
    update_session_locks,
    update_session_plan,
)

router = APIRouter(prefix="/trip", tags=["旅行规划"])


class ReviseRequest(BaseModel):
    session_id: str
    feedback: str
    locks: Optional[RevisionLocks] = None


class TripResponseWithSession(BaseModel):
    success: bool
    message: str
    session_id: str
    data: Any
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)
    locks: RevisionLocks = Field(default_factory=RevisionLocks)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_payload(locks: RevisionLocks) -> Dict[str, Any]:
    return locks.model_dump() if hasattr(locks, "model_dump") else locks.dict()


@router.post(
    "/plan",
    response_model=TripResponseWithSession,
    summary="生成旅行计划",
    description="并行执行专业 Agent，经过 GIS 路线优化和确定性 Validator 后按需自动修正行程",
)
async def plan_trip(request: TripRequest):
    """生成旅行计划，并返回完整 Agent / GIS / Validator 执行轨迹。"""
    try:
        effective_request = merge_constraints_from_text(request)
        planner = get_trip_planner_agent()
        trip_plan, execution_trace = execute_trip_plan(planner, effective_request)

        plan_dict = trip_plan.model_dump() if hasattr(trip_plan, "model_dump") else trip_plan.dict()
        empty_locks = RevisionLocks()
        session_id = create_session(plan_dict, execution_trace, _lock_payload(empty_locks))

        return TripResponseWithSession(
            success=True,
            message="旅行计划生成成功",
            session_id=session_id,
            data=trip_plan,
            execution_trace=execution_trace,
            locks=empty_locks,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成旅行计划失败: {exc}") from exc


@router.post(
    "/revise",
    response_model=TripResponseWithSession,
    summary="修改旅行计划",
    description="根据自然语言反馈修改现有计划，并用确定性 Lock Guard 保护已锁定内容",
)
async def revise_trip(request: ReviseRequest):
    """修改旅行计划；锁定内容即使被 LLM 误改，也会被后端恢复并记录。"""
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    effective_locks = merge_revision_locks(
        session.get("locks", {}),
        request.feedback,
        explicit=request.locks,
    )
    lock_payload = _lock_payload(effective_locks)

    planner = get_trip_planner_agent()
    started_at = _utc_now()
    started = time.perf_counter()
    revision_event: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "agent": "Revision Agent",
        "task": "根据用户反馈局部修改既有行程，同时遵守 revision locks",
        "tool": None,
        "status": "running",
        "started_at": started_at,
        "duration_ms": 0,
        "error": None,
        "locks": lock_payload,
    }

    try:
        candidate_plan = planner.revise_trip(
            session["current_plan"],
            request.feedback,
            effective_locks,
        )
        revision_event["status"] = "success"
        revision_event["result_preview"] = "Revision Agent 已生成局部修改候选方案"
    except Exception as exc:
        revision_event["status"] = "failed"
        revision_event["error"] = str(exc)
        raise HTTPException(status_code=500, detail=f"修改失败: {exc}") from exc
    finally:
        revision_event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        revision_event["finished_at"] = _utc_now()

    protected_plan, violations = enforce_revision_locks(
        session["current_plan"],
        candidate_plan,
        effective_locks,
    )
    lock_guard_event: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "agent": "Revision Lock Guard",
        "task": "确定性检查并保护锁定日期、酒店和景点",
        "tool": "deterministic_lock_rules",
        "status": "restored" if violations else "success",
        "started_at": _utc_now(),
        "finished_at": _utc_now(),
        "duration_ms": 0,
        "attempts": 1,
        "error": None,
        "locks": lock_payload,
        "violations": violations,
        "result_preview": (
            f"检测到 {len(violations)} 处锁定内容被 Agent 修改，已恢复原值。"
            if violations
            else "锁定内容保持不变。"
        ),
    }

    trace_events = [revision_event, lock_guard_event]
    updated = update_session_plan(
        request.session_id,
        protected_plan,
        feedback=request.feedback,
        execution_trace=trace_events,
        locks=lock_payload,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    return TripResponseWithSession(
        success=True,
        message="行程修改成功",
        session_id=request.session_id,
        data=protected_plan,
        execution_trace=trace_events,
        locks=effective_locks,
    )


@router.put(
    "/session/{session_id}/locks",
    summary="设置行程修改锁",
    description="显式设置锁定日期、全部酒店和指定景点；后续自然语言修改会持续遵守。",
)
async def set_revision_locks(session_id: str, locks: RevisionLocks):
    normalized = merge_revision_locks({}, "", explicit=locks)
    payload = _lock_payload(normalized)
    if not update_session_locks(session_id, payload):
        raise HTTPException(status_code=404, detail="会话不存在或已失效")
    return {
        "success": True,
        "session_id": session_id,
        "locks": payload,
    }


@router.get(
    "/session/{session_id}",
    summary="读取会话",
    description="读取当前计划、历史版本、revision locks 和完整 Agent 执行轨迹",
)
async def read_session(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    return {
        "success": True,
        "session_id": session_id,
        "data": session["current_plan"],
        "history": session["history"],
        "locks": session.get("locks", {}),
        "execution_trace": session["execution_trace"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
    }


@router.get(
    "/trace/{session_id}",
    summary="读取 Agent Execution Trace",
    description="返回该会话的 Agent、GIS Optimizer、Validator、Repair 与 Lock Guard 执行轨迹",
)
async def read_execution_trace(session_id: str):
    trace = get_session_trace(session_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    return {
        "success": True,
        "session_id": session_id,
        "execution_trace": trace,
    }


@router.get("/health")
async def health_check():
    try:
        get_trip_planner_agent()
        return {
            "status": "healthy",
            "service": "multi-agent-trip-planner",
            "persistence": "sqlite",
            "orchestration": "fan-out-fan-in-gis-validate-repair",
            "revision_guard": "persistent-locks",
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"服务不可用: {exc}") from exc
