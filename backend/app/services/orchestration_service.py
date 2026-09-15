"""多智能体任务编排与执行追踪。

这一层刻意与具体 Agent 实现解耦：Agent 负责完成单个专业任务，
Orchestrator 负责并发调度、错误隔离、耗时统计以及最终汇总。
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from ..models.schemas import TripPlan, TripRequest


TraceEvent = Dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_step(
    *,
    agent_name: str,
    task: str,
    tool: str | None,
    runner: Callable[[], str],
) -> Tuple[str, TraceEvent]:
    """运行一个 Agent 步骤，并把运行状态转换为统一 trace。"""
    event: TraceEvent = {
        "id": str(uuid.uuid4()),
        "agent": agent_name,
        "task": task,
        "tool": tool,
        "status": "running",
        "started_at": _utc_now(),
        "duration_ms": 0,
        "error": None,
    }
    started = time.perf_counter()

    try:
        result = runner()
        event["status"] = "success"
        # Trace 只保留短摘要，避免把完整模型输出重复塞进 API。
        event["result_preview"] = str(result)[:240]
        return result, event
    except Exception as exc:
        event["status"] = "failed"
        event["error"] = str(exc)
        # 单个检索 Agent 失败时不直接让整个任务崩溃，交由 Planner 决定如何降级。
        return f"{agent_name}执行失败：{exc}", event
    finally:
        event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        event["finished_at"] = _utc_now()


def execute_trip_plan(planner: Any, request: TripRequest) -> Tuple[TripPlan, List[TraceEvent]]:
    """并发执行检索型 Agent，汇总后再交给 Planner Agent。

    Attraction / Weather / Hotel 三个任务互不依赖，因此可以并行；
    Planner 必须等待三者结果后再执行，形成明确的 fan-out / fan-in 任务图。
    """
    trace: List[TraceEvent] = []
    total_started = time.perf_counter()

    attraction_query = planner._build_attraction_query(request)
    weather_query = f"请查询{request.city}的天气信息"
    hotel_query = f"请搜索{request.city}的{request.accommodation}酒店"

    jobs = {
        "attractions": {
            "agent_name": "Attraction Agent",
            "task": "搜索符合偏好的景点",
            "tool": "amap_maps_text_search",
            "runner": lambda: planner.attraction_agent.run(attraction_query),
        },
        "weather": {
            "agent_name": "Weather Agent",
            "task": "查询目的地天气",
            "tool": "amap_maps_weather",
            "runner": lambda: planner.weather_agent.run(weather_query),
        },
        "hotels": {
            "agent_name": "Hotel Agent",
            "task": "搜索符合住宿偏好的酒店",
            "tool": "amap_maps_text_search",
            "runner": lambda: planner.hotel_agent.run(hotel_query),
        },
    }

    results: Dict[str, str] = {}

    # 三个信息检索任务没有数据依赖，使用线程池降低整体等待时间。
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

    # 保证前端展示顺序稳定，不受线程完成先后影响。
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
        "error": None,
    }
    planner_started = time.perf_counter()

    try:
        planner_query = planner._build_planner_query(
            request,
            results.get("attractions", ""),
            results.get("weather", ""),
            results.get("hotels", ""),
        )
        planner_response = planner.planner_agent.run(planner_query)
        trip_plan = planner._parse_response(planner_response, request)
        planner_event["status"] = "success"
        planner_event["result_preview"] = str(planner_response)[:240]
    except Exception as exc:
        planner_event["status"] = "fallback"
        planner_event["error"] = str(exc)
        trip_plan = planner._create_fallback_plan(request)
    finally:
        planner_event["duration_ms"] = round((time.perf_counter() - planner_started) * 1000, 2)
        planner_event["finished_at"] = _utc_now()
        trace.append(planner_event)

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
            "error": None,
        }
    )

    return trip_plan, trace
