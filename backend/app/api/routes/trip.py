"""旅行规划 API 路由。"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...agents.trip_planner_agent import get_trip_planner_agent
from ...models.schemas import TripRequest
from ...services.constraint_service import merge_constraints_from_text
from ...services.orchestration_service import execute_trip_plan
from ...services.session_service import (
    create_session,
    get_session,
    get_session_trace,
    update_session_plan,
)

router = APIRouter(prefix="/trip", tags=["旅行规划"])


class ReviseRequest(BaseModel):
    session_id: str
    feedback: str


class TripResponseWithSession(BaseModel):
    success: bool
    message: str
    session_id: str
    data: Any
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post(
    "/plan",
    response_model=TripResponseWithSession,
    summary="生成旅行计划",
    description="并行执行专业 Agent，并在确定性 Validator 检查后按需自动修正行程",
)
async def plan_trip(request: TripRequest):
    """生成旅行计划，并返回完整 Agent / Validator 执行轨迹。"""
    try:
        effective_request = merge_constraints_from_text(request)
        planner = get_trip_planner_agent()
        trip_plan, execution_trace = execute_trip_plan(planner, effective_request)

        plan_dict = trip_plan.model_dump() if hasattr(trip_plan, "model_dump") else trip_plan.dict()
        session_id = create_session(plan_dict, execution_trace)

        return TripResponseWithSession(
            success=True,
            message="旅行计划生成成功",
            session_id=session_id,
            data=trip_plan,
            execution_trace=execution_trace,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成旅行计划失败: {exc}") from exc


@router.post(
    "/revise",
    response_model=TripResponseWithSession,
    summary="修改旅行计划",
    description="根据自然语言反馈修改现有计划，并记录修改步骤与历史版本",
)
async def revise_trip(request: ReviseRequest):
    """修改旅行计划，并把旧版本与执行轨迹保存到持久化会话。"""
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    planner = get_trip_planner_agent()
    started_at = _utc_now()
    started = time.perf_counter()
    trace_event: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "agent": "Revision Agent",
        "task": "根据用户反馈局部修改既有行程",
        "tool": None,
        "status": "running",
        "started_at": started_at,
        "duration_ms": 0,
        "error": None,
    }

    try:
        new_plan_dict = planner.revise_trip(session["current_plan"], request.feedback)
        trace_event["status"] = "success"
        trace_event["result_preview"] = "行程已根据反馈完成局部更新"
    except Exception as exc:
        trace_event["status"] = "failed"
        trace_event["error"] = str(exc)
        raise HTTPException(status_code=500, detail=f"修改失败: {exc}") from exc
    finally:
        trace_event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        trace_event["finished_at"] = _utc_now()

    updated = update_session_plan(
        request.session_id,
        new_plan_dict,
        feedback=request.feedback,
        execution_trace=[trace_event],
    )
    if not updated:
        raise HTTPException(status_code=404, detail="会话不存在或已失效")

    return TripResponseWithSession(
        success=True,
        message="行程修改成功",
        session_id=request.session_id,
        data=new_plan_dict,
        execution_trace=[trace_event],
    )


@router.get(
    "/session/{session_id}",
    summary="读取会话",
    description="读取当前计划、历史版本和完整 Agent 执行轨迹",
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
        "execution_trace": session["execution_trace"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
    }


@router.get(
    "/trace/{session_id}",
    summary="读取 Agent Execution Trace",
    description="返回该会话的 Agent、Validator、Repair 执行轨迹",
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
            "orchestration": "fan-out-fan-in-validate-repair",
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"服务不可用: {exc}") from exc
