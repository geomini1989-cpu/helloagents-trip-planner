"""多智能体任务编排、Local Knowledge、GIS 路线优化、执行追踪与验证修正闭环。"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from ..config import get_settings
from ..models.schemas import TripPlan, TripRequest
from .gis_optimizer_service import RouteOptimizationReport, optimize_trip_routes
from .resilience_service import AgentExecutionError, AttemptError, run_with_retry
from .validation_service import ValidationReport, validate_trip_plan


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


def _run_step(
    *,
    agent_name: str,
    task: str,
    tool: str | None,
    runner: Callable[[], str],
) -> Tuple[str, TraceEvent]:
    settings = get_settings()
    event: TraceEvent = {
        "id": str(uuid.uuid4()),
        "agent": agent_name,
        "task": task,
        "tool": tool,
        "status": "running",
        "started_at": _utc_now(),
        "duration_ms": 0,
        "attempts": 0,
        "error": None,
        "error_category": None,
        "retry_errors": [],
    }
    started = time.perf_counter()

    try:
        result, attempts, retry_errors = run_with_retry(
            runner,
            max_retries=settings.agent_max_retries,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )
        event["attempts"] = attempts
        event["retry_errors"] = _errors_to_dict(retry_errors)
        event["status"] = "success"
        event["result_preview"] = str(result)[:240]
        return result, event
    except AgentExecutionError as exc:
        event["status"] = "failed"
        event["attempts"] = exc.attempts
        event["error"] = str(exc)
        event["error_category"] = exc.category
        event["retry_errors"] = _errors_to_dict(exc.errors)
        return f"{agent_name}执行失败（{exc.category}）：{exc}", event
    finally:
        event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        event["finished_at"] = _utc_now()


def _run_local_knowledge(planner: Any, request: TripRequest, attraction_context: str) -> Tuple[str, TraceEvent]:
    settings = get_settings()
    event: TraceEvent = {
        "id": str(uuid.uuid4()),
        "agent": "Local Knowledge Agent",
        "task": "核验候选景点的开放时间、预约、闭馆、票务和临时公告",
        "tool": "tavily_web_search",
        "status": "running",
        "started_at": _utc_now(),
        "duration_ms": 0,
        "attempts": 0,
        "error": None,
        "error_category": None,
        "retry_errors": [],
    }
    started = time.perf_counter()
    try:
        (summary, knowledge), attempts, retry_errors = run_with_retry(
            lambda: planner.research_local_knowledge(request, attraction_context),
            max_retries=settings.agent_max_retries,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )
        event["attempts"] = attempts
        event["retry_errors"] = _errors_to_dict(retry_errors)
        event["knowledge_provider"] = knowledge.provider
        event["knowledge_sources"] = [
            {"title": item.title, "url": item.url, "score": item.score}
            for item in knowledge.sources
        ]
        if knowledge.degraded:
            event["status"] = "fallback"
            event["error"] = knowledge.error
            event["error_category"] = "local_knowledge_unavailable"
        else:
            event["status"] = "success"
        event["result_preview"] = summary[:700]
        return summary, event
    except AgentExecutionError as exc:
        event["status"] = "failed"
        event["attempts"] = exc.attempts
        event["error"] = str(exc)
        event["error_category"] = exc.category
        event["retry_errors"] = _errors_to_dict(exc.errors)
        return "Local Knowledge Agent 执行失败；不要猜测景点运营规则。", event
    finally:
        event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        event["finished_at"] = _utc_now()


def _validation_event(report: ValidationReport, *, agent_name: str, started_at: str, started: float) -> TraceEvent:
    report_dict = report.to_dict()
    status = "success" if report.passed else "needs_revision"
    return {
        "id": str(uuid.uuid4()),
        "agent": agent_name,
        "task": "校验预算、每日强度、重复景点与相邻景点交通耗时",
        "tool": "amap_route + deterministic_rules",
        "status": status,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "attempts": 1,
        "error": None,
        "error_category": None,
        "validation_report": report_dict,
        "result_preview": json.dumps(report_dict, ensure_ascii=False)[:500],
    }


def _gis_event(
    report: RouteOptimizationReport,
    *,
    agent_name: str,
    started_at: str,
    started: float,
) -> TraceEvent:
    report_dict = report.to_dict()
    return {
        "id": str(uuid.uuid4()),
        "agent": agent_name,
        "task": "基于 GIS 坐标与路网时间矩阵优化每日景点访问顺序",
        "tool": "amap_route_matrix + haversine + exact_route_search",
        "status": "success",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "attempts": 1,
        "error": None,
        "error_category": None,
        "route_optimization": report_dict,
        "result_preview": json.dumps(report_dict, ensure_ascii=False)[:700],
    }


def _run_gis_optimization(
    trip_plan: TripPlan,
    request: TripRequest,
    *,
    agent_name: str,
) -> tuple[TripPlan, TraceEvent]:
    started_at = _utc_now()
    started = time.perf_counter()
    try:
        optimized, report = optimize_trip_routes(trip_plan, request)
        return optimized, _gis_event(
            report,
            agent_name=agent_name,
            started_at=started_at,
            started=started,
        )
    except Exception as exc:
        return trip_plan, {
            "id": str(uuid.uuid4()),
            "agent": agent_name,
            "task": "基于 GIS 坐标与路网时间矩阵优化每日景点访问顺序",
            "tool": "amap_route_matrix + haversine + exact_route_search",
            "status": "failed",
            "started_at": started_at,
            "finished_at": _utc_now(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "attempts": 1,
            "error": str(exc),
            "error_category": "gis_optimization_failed",
            "result_preview": "GIS 优化失败，保留 Planner 原始顺序并继续 Validator。",
        }


def execute_trip_plan(planner: Any, request: TripRequest) -> Tuple[TripPlan, List[TraceEvent]]:
    """执行 retrieval fan-out/fan-in → Local Knowledge → Planner → GIS → Validator → optional Repair。

    Local Knowledge 依赖 Attraction 候选，因此在第一批并行检索完成后执行。
    自动修正最多执行一次，避免 Agent 在“生成—检查—重写”之间无限循环。
    """
    settings = get_settings()
    trace: List[TraceEvent] = []
    total_started = time.perf_counter()
    agents = planner.create_request_agents()

    attraction_query = planner._build_attraction_query(request)
    weather_query = f"请查询{request.city}的天气信息"
    hotel_query = f"请搜索{request.city}的{request.accommodation}酒店"

    jobs = {
        "attractions": {
            "agent_name": "Attraction Agent",
            "task": "搜索符合偏好的景点",
            "tool": "amap_maps_text_search",
            "runner": lambda: agents["attraction"].run(attraction_query),
        },
        "weather": {
            "agent_name": "Weather Agent",
            "task": "查询目的地天气",
            "tool": "amap_maps_weather",
            "runner": lambda: agents["weather"].run(weather_query),
        },
        "hotels": {
            "agent_name": "Hotel Agent",
            "task": "搜索符合住宿偏好的酒店",
            "tool": "amap_maps_text_search",
            "runner": lambda: agents["hotel"].run(hotel_query),
        },
    }

    results: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="trip-agent") as executor:
        future_to_key = {
            executor.submit(
                _run_step,
                agent_name=job["agent_name"],
                task=job["task"],
                tool=job["tool"],
                runner=job["runner"],
            ): key
            for key, job in jobs.items()
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            result, event = future.result()
            results[key] = result
            trace.append(event)

    order = {"Attraction Agent": 0, "Weather Agent": 1, "Hotel Agent": 2}
    trace.sort(key=lambda item: order.get(item["agent"], 99))

    attraction_ok = any(
        item.get("agent") == "Attraction Agent" and item.get("status") == "success"
        for item in trace
    )
    if attraction_ok:
        local_knowledge, knowledge_event = _run_local_knowledge(
            planner,
            request,
            results.get("attractions", ""),
        )
    else:
        now = _utc_now()
        local_knowledge = "景点检索失败，未执行 Local Knowledge；不要猜测景点运营规则。"
        knowledge_event = {
            "id": str(uuid.uuid4()),
            "agent": "Local Knowledge Agent",
            "task": "核验候选景点运营规则",
            "tool": "tavily_web_search",
            "status": "fallback",
            "started_at": now,
            "finished_at": now,
            "duration_ms": 0,
            "attempts": 0,
            "error": "Attraction Agent unavailable",
            "error_category": "dependency_unavailable",
            "knowledge_sources": [],
            "result_preview": local_knowledge,
        }
    results["local_knowledge"] = local_knowledge
    trace.append(knowledge_event)

    planner_event: TraceEvent = {
        "id": str(uuid.uuid4()),
        "agent": "Planner Agent",
        "task": "汇总多 Agent、Local Knowledge 与硬约束生成结构化行程",
        "tool": None,
        "status": "running",
        "started_at": _utc_now(),
        "duration_ms": 0,
        "attempts": 0,
        "error": None,
        "error_category": None,
        "retry_errors": [],
    }
    planner_started = time.perf_counter()
    planner_query = planner._build_planner_query(
        request,
        results.get("attractions", ""),
        results.get("weather", ""),
        results.get("hotels", ""),
        results.get("local_knowledge", ""),
    )

    def _planner_once() -> tuple[str, TripPlan]:
        response = planner.create_planner_agent().run(planner_query)
        parsed = planner._parse_response(response, request=None)
        return response, parsed

    try:
        (planner_response, trip_plan), attempts, retry_errors = run_with_retry(
            _planner_once,
            max_retries=settings.agent_max_retries,
            backoff_seconds=settings.agent_retry_backoff_seconds,
            retry_categories={"timeout", "rate_limit", "network", "agent_error", "validation"},
        )
        planner_event["attempts"] = attempts
        planner_event["retry_errors"] = _errors_to_dict(retry_errors)
        planner_event["status"] = "success"
        planner_event["result_preview"] = str(planner_response)[:240]
    except AgentExecutionError as exc:
        planner_event["status"] = "fallback"
        planner_event["attempts"] = exc.attempts
        planner_event["error"] = str(exc)
        planner_event["error_category"] = exc.category
        planner_event["retry_errors"] = _errors_to_dict(exc.errors)
        trip_plan = planner._create_fallback_plan(request)
    finally:
        planner_event["duration_ms"] = round((time.perf_counter() - planner_started) * 1000, 2)
        planner_event["finished_at"] = _utc_now()
        trace.append(planner_event)

    final_validation_passed = planner_event["status"] == "success"

    if planner_event["status"] == "success":
        trip_plan, gis_event = _run_gis_optimization(
            trip_plan,
            request,
            agent_name="GIS Route Optimizer",
        )
        trace.append(gis_event)

        validation_started_at = _utc_now()
        validation_started = time.perf_counter()
        report = validate_trip_plan(trip_plan, request, check_routes=True)
        trace.append(_validation_event(
            report,
            agent_name="Trip Validator",
            started_at=validation_started_at,
            started=validation_started,
        ))
        final_validation_passed = report.passed

        if report.blocking_issues:
            repair_event: TraceEvent = {
                "id": str(uuid.uuid4()),
                "agent": "Repair Agent",
                "task": "根据 Validator 问题做一次最小范围自动修正",
                "tool": None,
                "status": "running",
                "started_at": _utc_now(),
                "duration_ms": 0,
                "attempts": 1,
                "error": None,
                "error_category": None,
            }
            repair_started = time.perf_counter()
            try:
                repaired = planner.repair_trip_plan(
                    trip_plan,
                    request,
                    [issue.to_dict() for issue in report.blocking_issues],
                )
                trip_plan = repaired
                repair_event["status"] = "success"
                repair_event["result_preview"] = "已依据 Validator 报告完成一次最小修正"
            except Exception as exc:
                repair_event["status"] = "failed"
                repair_event["error"] = str(exc)
                repair_event["error_category"] = "repair_failed"
            finally:
                repair_event["duration_ms"] = round((time.perf_counter() - repair_started) * 1000, 2)
                repair_event["finished_at"] = _utc_now()
                trace.append(repair_event)

            if repair_event["status"] == "success":
                trip_plan, post_gis_event = _run_gis_optimization(
                    trip_plan,
                    request,
                    agent_name="Post-Repair GIS Optimizer",
                )
                trace.append(post_gis_event)

                revalidate_started_at = _utc_now()
                revalidate_started = time.perf_counter()
                final_report = validate_trip_plan(trip_plan, request, check_routes=True)
                trace.append(_validation_event(
                    final_report,
                    agent_name="Post-Repair Validator",
                    started_at=revalidate_started_at,
                    started=revalidate_started,
                ))
                final_validation_passed = final_report.passed
            else:
                final_validation_passed = False

    degraded = (
        any(item["status"] in {"failed", "fallback"} for item in trace)
        or not final_validation_passed
    )
    retried_steps = sum(1 for item in trace if int(item.get("attempts", 1) or 1) > 1)

    trace.append({
        "id": str(uuid.uuid4()),
        "agent": "Orchestrator",
        "task": "完成多源检索、本地知识核验、规划、GIS 优化、校验与可选自动修正",
        "tool": None,
        "status": "degraded" if degraded else "success",
        "started_at": trace[0]["started_at"] if trace else _utc_now(),
        "finished_at": _utc_now(),
        "duration_ms": round((time.perf_counter() - total_started) * 1000, 2),
        "attempts": 1,
        "error": None,
        "error_category": None,
        "degraded": degraded,
        "validation_passed": final_validation_passed,
        "retried_steps": retried_steps,
    })

    return trip_plan, trace
