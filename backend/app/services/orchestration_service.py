"""多智能体任务编排与执行追踪。

Agent 负责完成单个专业任务；Orchestrator 负责并发调度、错误隔离、
重试策略、耗时统计以及最终汇总。
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from ..config import get_settings
from ..models.schemas import TripPlan, TripRequest
from .resilience_service import AgentExecutionError, AttemptError, run_with_retry


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
    """运行一个检索 Agent，并转换为统一、可评估的 trace 事件。"""
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


def execute_trip_plan(planner: Any, request: TripRequest) -> Tuple[TripPlan, List[TraceEvent]]:
    """并发执行检索型 Agent，fan-in 后交给 Planner Agent。

    每次调用先创建 request-scoped Agent，避免 SimpleAgent `_history`
    在不同业务请求之间串扰。
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

    planner_event: TraceEvent = {
        "id": str(uuid.uuid4()),
        "agent": "Planner Agent",
        "task": "汇总多 Agent 结果并生成结构化行程",
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
    )

    def _planner_once() -> tuple[str, TripPlan]:
        # 每次重新生成都使用新的 Planner Agent，避免把上一次非法输出带入下一次尝试。
        planner_agent = planner.create_planner_agent()
        response = planner_agent.run(planner_query)
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

    degraded = any(item["status"] in {"failed", "fallback"} for item in trace)
    retried_steps = sum(1 for item in trace if int(item.get("attempts", 1) or 1) > 1)

    trace.append(
        {
            "id": str(uuid.uuid4()),
            "agent": "Orchestrator",
            "task": "完成任务编排",
            "tool": None,
            "status": "success",
            "started_at": trace[0]["started_at"] if trace else _utc_now(),
            "finished_at": _utc_now(),
            "duration_ms": round((time.perf_counter() - total_started) * 1000, 2),
            "attempts": 1,
            "error": None,
            "error_category": None,
            "degraded": degraded,
            "retried_steps": retried_steps,
        }
    )

    return trip_plan, trace
