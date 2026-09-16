"""Execute validated Coordinator plans for existing trip sessions."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from ..config import get_settings
from ..models.schemas import RevisionLocks, TripConstraints, TripPlan, TripRequest
from .coordinator_service import ExecutionPlan
from .gis_optimizer_service import optimize_trip_routes
from .resilience_service import AgentExecutionError, AttemptError, run_with_retry
from .revision_lock_service import enforce_revision_locks
from .validation_service import validate_trip_plan


TraceEvent = Dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _errors_to_dict(errors: List[AttemptError]) -> List[Dict[str, Any]]:
    return [
        {
            "attempt": item.attempt,
            "category": item.category,
            "message": item.message,
            "retryable": item.retryable,
        }
        for item in errors
    ]


def coordinator_trace_event(plan: ExecutionPlan) -> TraceEvent:
    now = _utc_now()
    return {
        "id": str(uuid.uuid4()),
        "agent": "Coordinator",
        "task": "识别用户任务并生成受白名单约束的 Execution Plan",
        "tool": None,
        "status": "success" if plan.source == "llm" else "fallback",
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "attempts": 1,
        "error": None,
        "error_category": None,
        "execution_plan": plan.to_dict(),
        "result_preview": json.dumps(plan.to_dict(), ensure_ascii=False)[:700],
    }


def request_from_session(session: Dict[str, Any]) -> TripRequest:
    """Recover the original request; infer a conservative request for legacy sessions."""
    persisted = session.get("request")
    if persisted:
        return TripRequest(**persisted)

    plan = session["current_plan"]
    days = plan.get("days") or []
    first_day = days[0] if days else {}
    return TripRequest(
        city=plan.get("city", ""),
        start_date=plan.get("start_date", ""),
        end_date=plan.get("end_date", plan.get("start_date", "")),
        travel_days=max(1, len(days)),
        transportation=first_day.get("transportation") or "公共交通",
        accommodation=first_day.get("accommodation") or "未指定",
        preferences=[],
        free_text_input="",
        constraints=TripConstraints(),
    )


def _run_retrieval(
    *,
    agent_name: str,
    task: str,
    tool: str,
    runner: Any,
) -> Tuple[Any, TraceEvent]:
    settings = get_settings()
    started_at = _utc_now()
    started = time.perf_counter()
    event: TraceEvent = {
        "id": str(uuid.uuid4()),
        "agent": agent_name,
        "task": task,
        "tool": tool,
        "status": "running",
        "started_at": started_at,
        "duration_ms": 0,
        "attempts": 0,
        "error": None,
        "error_category": None,
        "retry_errors": [],
    }
    try:
        result, attempts, retry_errors = run_with_retry(
            runner,
            max_retries=settings.agent_max_retries,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )
        event["attempts"] = attempts
        event["retry_errors"] = _errors_to_dict(retry_errors)
        event["status"] = "success"
        event["result_preview"] = str(result)[:300]
        return result, event
    except AgentExecutionError as exc:
        event["attempts"] = exc.attempts
        event["retry_errors"] = _errors_to_dict(exc.errors)
        event["status"] = "failed"
        event["error"] = str(exc)
        event["error_category"] = exc.category
        return f"{agent_name}执行失败（{exc.category}）：{exc}", event
    finally:
        event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        event["finished_at"] = _utc_now()


def _lock_event(locks: RevisionLocks, violations: List[Dict[str, Any]], *, stage: str) -> TraceEvent:
    now = _utc_now()
    locks_payload = locks.model_dump() if hasattr(locks, "model_dump") else locks.dict()
    return {
        "id": str(uuid.uuid4()),
        "agent": "Revision Lock Guard",
        "task": f"{stage}后确定性保护锁定日期、酒店和景点",
        "tool": "deterministic_lock_rules",
        "status": "restored" if violations else "success",
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "attempts": 1,
        "error": None,
        "locks": locks_payload,
        "violations": violations,
        "result_preview": (
            f"检测到 {len(violations)} 处锁定内容变化，已恢复。"
            if violations
            else "锁定内容保持不变。"
        ),
    }


def _attraction_context_from_plan(plan: Dict[str, Any]) -> str:
    """Build compact POI context for rule verification without another AMap retrieval."""
    items: List[str] = []
    for day in plan.get("days") or []:
        for attraction in day.get("attractions") or []:
            name = str(attraction.get("name") or "").strip()
            address = str(attraction.get("address") or "").strip()
            if name:
                items.append(f"{name}（{address}）" if address else name)
    return "；".join(dict.fromkeys(items))


def execute_session_task(
    planner: Any,
    session: Dict[str, Any],
    feedback: str,
    locks: RevisionLocks,
    execution_plan: ExecutionPlan,
) -> Tuple[Dict[str, Any], List[TraceEvent], bool]:
    """Execute one canonical task graph against an existing session.

    The Coordinator selects an intent; this function executes only backend-owned
    capabilities from the validated graph. No LLM-produced function/tool name is invoked.
    """
    trace: List[TraceEvent] = [coordinator_trace_event(execution_plan)]
    total_started = time.perf_counter()
    original_plan = session["current_plan"]
    request = request_from_session(session)
    candidate_plan = original_plan
    context_sections: List[str] = []

    capabilities = set(execution_plan.capabilities)
    agents = None
    if "weather" in capabilities or "hotel" in capabilities:
        agents = planner.create_request_agents()

    if "weather" in capabilities and agents is not None:
        weather, event = _run_retrieval(
            agent_name="Weather Agent",
            task="为当前修改请求刷新目的地天气上下文",
            tool="amap_maps_weather",
            runner=lambda: agents["weather"].run(f"请查询{request.city}的天气信息"),
        )
        trace.append(event)
        context_sections.append(f"最新天气工具结果：\n{weather}")

    if "hotel" in capabilities and agents is not None:
        hotel, event = _run_retrieval(
            agent_name="Hotel Agent",
            task="为当前修改请求刷新酒店候选上下文",
            tool="amap_maps_text_search",
            runner=lambda: agents["hotel"].run(f"请搜索{request.city}的{request.accommodation}酒店"),
        )
        trace.append(event)
        context_sections.append(f"最新酒店工具结果：\n{hotel}")

    if "local_knowledge" in capabilities:
        attraction_context = _attraction_context_from_plan(original_plan)
        knowledge_result, event = _run_retrieval(
            agent_name="Local Knowledge Agent",
            task="核验当前行程景点的开放时间、预约、闭馆、票务与临时公告",
            tool="tavily_web_search",
            runner=lambda: planner.research_local_knowledge(request, attraction_context),
        )
        if isinstance(knowledge_result, tuple) and len(knowledge_result) == 2:
            knowledge_summary, knowledge_meta = knowledge_result
            event["knowledge_provider"] = knowledge_meta.provider
            event["knowledge_sources"] = [
                {"title": item.title, "url": item.url, "score": item.score}
                for item in knowledge_meta.sources
            ]
            event["knowledge_claims"] = [item.to_dict() for item in knowledge_meta.claims]
            event["knowledge_claim_metrics"] = knowledge_meta.claim_metrics()
            event["result_preview"] = str(knowledge_summary)[:1000]
            if knowledge_meta.degraded:
                event["status"] = "fallback"
                event["error"] = knowledge_meta.error
                event["error_category"] = "local_knowledge_unavailable"
            elif knowledge_meta.claim_parse_error:
                event["status"] = "fallback"
                event["error"] = knowledge_meta.claim_parse_error
                event["error_category"] = "local_knowledge_schema_invalid"
            context_sections.append(
                "最新景点运营规则核验结果：\n"
                f"{knowledge_summary}\n"
                "仅把 verified_claims 视为事实；unverified_claims 只能作为提醒。"
            )
        else:
            context_sections.append(
                "Local Knowledge Agent 未获得可靠结果；不要猜测开放时间、预约或闭馆规则。"
            )
        trace.append(event)

    if "revision" in capabilities:
        revision_started_at = _utc_now()
        revision_started = time.perf_counter()
        enriched_feedback = feedback
        if context_sections:
            enriched_feedback += "\n\n以下是 Coordinator 根据任务动态获取的实时上下文，请据此修改：\n" + "\n\n".join(context_sections)
        event: TraceEvent = {
            "id": str(uuid.uuid4()),
            "agent": "Revision Agent",
            "task": "根据用户反馈和动态检索上下文做最小范围局部修改",
            "tool": None,
            "status": "running",
            "started_at": revision_started_at,
            "duration_ms": 0,
            "attempts": 1,
            "error": None,
            "error_category": None,
        }
        try:
            candidate_plan = planner.revise_trip(original_plan, enriched_feedback, locks)
            event["status"] = "success"
            event["result_preview"] = "Revision Agent 已生成动态任务图下的修改候选。"
        except Exception as exc:
            event["status"] = "failed"
            event["error"] = str(exc)
            event["error_category"] = "revision_failed"
            candidate_plan = original_plan
        finally:
            event["duration_ms"] = round((time.perf_counter() - revision_started) * 1000, 2)
            event["finished_at"] = _utc_now()
            trace.append(event)

        candidate_plan, violations = enforce_revision_locks(original_plan, candidate_plan, locks)
        trace.append(_lock_event(locks, violations, stage="Revision"))

    if "gis" in capabilities:
        gis_started_at = _utc_now()
        gis_started = time.perf_counter()
        event: TraceEvent = {
            "id": str(uuid.uuid4()),
            "agent": "GIS Route Optimizer",
            "task": "根据当前任务图优化已有景点访问顺序",
            "tool": "amap_route_matrix + haversine + exact_route_search",
            "status": "running",
            "started_at": gis_started_at,
            "duration_ms": 0,
            "attempts": 1,
            "error": None,
            "error_category": None,
        }
        try:
            optimized, report = optimize_trip_routes(TripPlan(**candidate_plan), request)
            optimized_dict = optimized.model_dump() if hasattr(optimized, "model_dump") else optimized.dict()
            optimized_dict, gis_lock_violations = enforce_revision_locks(original_plan, optimized_dict, locks)
            candidate_plan = optimized_dict
            event["status"] = "success"
            event["route_optimization"] = report.to_dict()
            event["result_preview"] = json.dumps(report.to_dict(), ensure_ascii=False)[:700]
            if gis_lock_violations:
                trace.append(_lock_event(locks, gis_lock_violations, stage="GIS"))
        except Exception as exc:
            event["status"] = "failed"
            event["error"] = str(exc)
            event["error_category"] = "gis_optimization_failed"
        finally:
            event["duration_ms"] = round((time.perf_counter() - gis_started) * 1000, 2)
            event["finished_at"] = _utc_now()
            trace.append(event)

    validation_passed = True
    if "validator" in capabilities:
        validation_started_at = _utc_now()
        validation_started = time.perf_counter()
        try:
            report = validate_trip_plan(TripPlan(**candidate_plan), request, check_routes=True)
            validation_passed = report.passed
            report_dict = report.to_dict()
            trace.append({
                "id": str(uuid.uuid4()),
                "agent": "Trip Validator",
                "task": "校验动态修改后的预算、强度、重复景点和路线约束",
                "tool": "amap_route + deterministic_rules",
                "status": "success" if report.passed else "needs_revision",
                "started_at": validation_started_at,
                "finished_at": _utc_now(),
                "duration_ms": round((time.perf_counter() - validation_started) * 1000, 2),
                "attempts": 1,
                "error": None,
                "validation_report": report_dict,
                "result_preview": json.dumps(report_dict, ensure_ascii=False)[:700],
            })
        except Exception as exc:
            validation_passed = False
            trace.append({
                "id": str(uuid.uuid4()),
                "agent": "Trip Validator",
                "task": "校验动态修改后的行程",
                "tool": "deterministic_rules",
                "status": "failed",
                "started_at": validation_started_at,
                "finished_at": _utc_now(),
                "duration_ms": round((time.perf_counter() - validation_started) * 1000, 2),
                "attempts": 1,
                "error": str(exc),
                "error_category": "validation_failed",
            })

    degraded = any(item.get("status") in {"failed", "fallback", "needs_revision"} for item in trace) or not validation_passed
    trace.append({
        "id": str(uuid.uuid4()),
        "agent": "Dynamic Orchestrator",
        "task": "按 Coordinator Execution Plan 完成动态 Agent 编排",
        "tool": None,
        "status": "degraded" if degraded else "success",
        "started_at": trace[0]["started_at"],
        "finished_at": _utc_now(),
        "duration_ms": round((time.perf_counter() - total_started) * 1000, 2),
        "attempts": 1,
        "error": None,
        "degraded": degraded,
        "validation_passed": validation_passed,
        "execution_plan": execution_plan.to_dict(),
    })
    return candidate_plan, trace, degraded
