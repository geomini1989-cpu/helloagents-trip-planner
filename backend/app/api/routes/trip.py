"""旅行规划 API 路由。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...agents.trip_planner_agent import get_trip_planner_agent
from ...models.schemas import RevisionLocks, TripRequest
from ...services.constraint_service import merge_constraints_from_text
from ...services.coordinator_service import build_execution_plan
from ...services.dynamic_orchestration_service import coordinator_trace_event, execute_session_task
from ...services.orchestration_service import execute_trip_plan
from ...services.revision_lock_service import merge_revision_locks
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


class DispatchRequest(BaseModel):
    """统一自然语言任务入口。

    没有 session_id 时需要 trip_request，Coordinator 会进入 full_plan；
    有 session_id 时会在已有计划上动态选择 Weather/Hotel/Revision/GIS/Validator。
    """

    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    trip_request: Optional[TripRequest] = None
    locks: Optional[RevisionLocks] = None


class TripResponseWithSession(BaseModel):
    success: bool
    message: str
    session_id: str
    data: Any
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)
    locks: RevisionLocks = Field(default_factory=RevisionLocks)
    execution_plan: Optional[Dict[str, Any]] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_payload(locks: RevisionLocks) -> Dict[str, Any]:
    return locks.model_dump() if hasattr(locks, "model_dump") else locks.dict()


def _model_dump(value: Any) -> Dict[str, Any]:
    return value.model_dump() if hasattr(value, "model_dump") else value.dict()


def _append_message_to_request(request: TripRequest, message: str) -> TripRequest:
    payload = _model_dump(request)
    existing = str(payload.get("free_text_input") or "").strip()
    payload["free_text_input"] = "；".join(part for part in [existing, message.strip()] if part)
    return TripRequest(**payload)


def _execute_existing_session(
    *,
    session: Dict[str, Any],
    feedback: str,
    explicit_locks: Optional[RevisionLocks],
) -> tuple[Dict[str, Any], List[Dict[str, Any]], RevisionLocks, Dict[str, Any]]:
    effective_locks = merge_revision_locks(
        session.get("locks", {}),
        feedback,
        explicit=explicit_locks,
    )
    execution_plan = build_execution_plan(feedback, has_session=True)
    planner = get_trip_planner_agent()
    protected_plan, trace_events, _ = execute_session_task(
        planner,
        session,
        feedback,
        effective_locks,
        execution_plan,
    )
    return protected_plan, trace_events, effective_locks, execution_plan.to_dict()


@router.post(
    "/plan",
    response_model=TripResponseWithSession,
    summary="生成旅行计划",
    description="并行执行专业 Agent，经过 GIS 路线优化和确定性 Validator 后按需自动修正行程",
)
async def plan_trip(request: TripRequest):
    """显式完整规划入口；适合表单式前端。"""
    try:
        effective_request = merge_constraints_from_text(request)
        planner = get_trip_planner_agent()
        trip_plan, execution_trace = execute_trip_plan(planner, effective_request)

        plan_dict = _model_dump(trip_plan)
        empty_locks = RevisionLocks()
        session_id = create_session(
            plan_dict,
            execution_trace,
            _lock_payload(empty_locks),
            request=_model_dump(effective_request),
        )

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
    "/dispatch",
    response_model=TripResponseWithSession,
    summary="Coordinator 动态任务入口",
    description="根据自然语言任务生成白名单 Execution Plan，并动态选择需要的 Agent / GIS / Validator。",
)
async def dispatch_trip_task(request: DispatchRequest):
    """统一入口：新任务走完整规划，已有 Session 走动态任务图。"""
    if request.session_id:
        session = get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在或已失效")
        try:
            plan, trace, locks, execution_plan = _execute_existing_session(
                session=session,
                feedback=request.message,
                explicit_locks=request.locks,
            )
            lock_payload = _lock_payload(locks)
            updated = update_session_plan(
                request.session_id,
                plan,
                feedback=request.message,
                execution_trace=trace,
                locks=lock_payload,
            )
            if not updated:
                raise HTTPException(status_code=404, detail="会话不存在或已失效")
            return TripResponseWithSession(
                success=True,
                message="动态任务执行完成",
                session_id=request.session_id,
                data=plan,
                execution_trace=trace,
                locks=locks,
                execution_plan=execution_plan,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"动态任务执行失败: {exc}") from exc

    if request.trip_request is None:
        raise HTTPException(status_code=400, detail="新任务需要提供 trip_request；已有计划请提供 session_id")

    try:
        execution_plan = build_execution_plan(request.message, has_session=False)
        effective_request = merge_constraints_from_text(
            _append_message_to_request(request.trip_request, request.message)
        )
        planner = get_trip_planner_agent()
        trip_plan, base_trace = execute_trip_plan(planner, effective_request)
        trace = [coordinator_trace_event(execution_plan), *base_trace]
        empty_locks = RevisionLocks()
        session_id = create_session(
            _model_dump(trip_plan),
            trace,
            _lock_payload(empty_locks),
            request=_model_dump(effective_request),
        )
        return TripResponseWithSession(
            success=True,
            message="Coordinator 已完成完整旅行规划",
            session_id=session_id,
            data=trip_plan,
            execution_trace=trace,
            locks=empty_locks,
            execution_plan=execution_plan.to_dict(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"动态规划失败: {exc}") from exc


@router.post(
    "/revise",
    response_model=TripResponseWithSession,
    summary="动态修改旅行计划",
    description="Coordinator 根据反馈动态选择 Weather/Hotel/Revision/GIS/Validator，并用 Lock Guard 保护已锁定内容。",
)
async def revise_trip(request: ReviseRequest):
    """保持原 API 兼容，但内部已经升级为动态任务编排。"""
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    try:
        protected_plan, trace_events, effective_locks, execution_plan = _execute_existing_session(
            session=session,
            feedback=request.feedback,
            explicit_locks=request.locks,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"修改失败: {exc}") from exc

    lock_payload = _lock_payload(effective_locks)
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
        execution_plan=execution_plan,
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
    description="读取原始请求、当前计划、历史版本、revision locks 和完整 Agent 执行轨迹",
)
async def read_session(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    return {
        "success": True,
        "session_id": session_id,
        "request": session.get("request", {}),
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
    description="返回该会话的 Coordinator、Agent、GIS Optimizer、Validator、Repair 与 Lock Guard 执行轨迹",
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
            "orchestration": "coordinator + validated-task-graph + fan-out-fan-in-gis-validate-repair",
            "revision_guard": "persistent-locks",
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"服务不可用: {exc}") from exc
