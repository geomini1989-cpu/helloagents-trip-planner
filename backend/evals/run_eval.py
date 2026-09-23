"""Deterministic evaluation runner for the Multi-Agent Trip Planner.

Measures structured output, retrieval success, Local Knowledge source grounding,
retry/fallback behavior, deterministic constraint validation, automatic repair outcomes
and latency without LLM-as-a-Judge.

Run from backend/:
    python -m evals.run_eval
    python -m evals.run_eval --limit 3
    python -m evals.run_eval --case beijing-culture-3d
    python -m evals.run_eval --min-pass-rate 0.8
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.agents.trip_planner_agent import get_trip_planner_agent
from app.models.schemas import TripPlan, TripRequest
from app.services.constraint_service import merge_constraints_from_text
from app.services.orchestration_service import execute_trip_plan


EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = EVAL_DIR / "cases.json"
DEFAULT_REPORT = EVAL_DIR / "reports" / "latest.json"
RETRIEVAL_AGENTS = {"Attraction Agent", "Weather Agent", "Hotel Agent"}


def _percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 2)


def load_cases(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError("eval cases must be a JSON array")
    return data


def build_request(case: Dict[str, Any]) -> TripRequest:
    start = date.today() + timedelta(days=int(case.get("start_offset_days", 1)))
    travel_days = int(case["travel_days"])
    end = start + timedelta(days=travel_days - 1)

    request = TripRequest(
        city=case["city"],
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        travel_days=travel_days,
        transportation=case["transportation"],
        accommodation=case["accommodation"],
        preferences=case.get("preferences", []),
        free_text_input=case.get("free_text_input", ""),
        constraints=case.get("constraints", {}),
    )
    return merge_constraints_from_text(request)


def _all_coordinates_valid(plan: TripPlan) -> bool:
    for day in plan.days:
        for attraction in day.attractions:
            location = attraction.location
            if not (-180 <= location.longitude <= 180):
                return False
            if not (-90 <= location.latitude <= 90):
                return False
    return True


def _all_days_have_meals(plan: TripPlan) -> bool:
    required = {"breakfast", "lunch", "dinner"}
    return all(required.issubset({meal.type for meal in day.meals}) for day in plan.days)


def _budget_consistent(plan: TripPlan) -> bool:
    if plan.budget is None:
        return False
    budget = plan.budget
    components = budget.total_attractions + budget.total_hotels + budget.total_meals + budget.total_transportation
    return budget.total == components and min(
        budget.total_attractions,
        budget.total_hotels,
        budget.total_meals,
        budget.total_transportation,
        budget.total,
    ) >= 0


def _final_validation_event(trace: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    post_repair_events = [
        item
        for item in trace
        if str(item.get("agent") or "").startswith("Post-Repair Validator")
    ]
    if post_repair_events:
        return post_repair_events[-1]

    events = [item for item in trace if item.get("agent") == "Trip Validator"]
    return events[-1] if events else None


def evaluate_case(
    case: Dict[str, Any],
    request: TripRequest,
    plan: TripPlan,
    trace: List[Dict[str, Any]],
) -> Dict[str, Any]:
    planner_event = next((item for item in trace if item.get("agent") == "Planner Agent"), None)
    retrieval_events = [item for item in trace if item.get("agent") in RETRIEVAL_AGENTS]
    knowledge_event = next((item for item in trace if item.get("agent") == "Local Knowledge Agent"), None)
    orchestrator_event = next((item for item in trace if item.get("agent") == "Orchestrator"), None)
    validation_event = _final_validation_event(trace)
    repair_events = [item for item in trace if item.get("agent") == "Repair Agent"]
    repair_event = repair_events[-1] if repair_events else None

    expected_min_attractions = int(case.get("expected_min_attractions", 1))
    require_budget = bool(case.get("require_budget", False))
    require_weather = bool(case.get("require_weather", False))
    validation_passed = bool(
        validation_event
        and validation_event.get("validation_report", {}).get("passed") is True
    )

    knowledge_metrics = (knowledge_event or {}).get("knowledge_claim_metrics", {}) or {}
    unsupported_claims = int(knowledge_metrics.get("unsupported_claims", 0) or 0)
    claim_parse_error = knowledge_metrics.get("claim_parse_error")
    fallback_used = any(item.get("status") == "fallback" for item in trace)

    checks = {
        "city_matches_request": plan.city == request.city,
        "day_count_matches_request": len(plan.days) == request.travel_days,
        "day_indexes_are_sequential": [day.day_index for day in plan.days] == list(range(request.travel_days)),
        "minimum_attractions_per_day": all(len(day.attractions) >= expected_min_attractions for day in plan.days),
        "breakfast_lunch_dinner_present": _all_days_have_meals(plan),
        "coordinates_are_valid": _all_coordinates_valid(plan),
        "weather_coverage": (not require_weather) or len(plan.weather_info) >= request.travel_days,
        "budget_present": (not require_budget) or plan.budget is not None,
        "budget_is_consistent": (not require_budget) or _budget_consistent(plan),
        "all_retrieval_agents_succeeded": len(retrieval_events) == 3
        and all(item.get("status") == "success" for item in retrieval_events),
        "planner_structured_output_succeeded": planner_event is not None and planner_event.get("status") == "success",
        "local_knowledge_has_no_unsupported_claims": unsupported_claims == 0 and not claim_parse_error,
        "deterministic_validation_passed": validation_passed,
        "no_fallback_used": not fallback_used,
    }

    passed_checks = sum(1 for passed in checks.values() if passed)
    total_checks = len(checks)
    failed_checks = [name for name, passed in checks.items() if not passed]

    return {
        "id": case["id"],
        "request": request.model_dump(),
        "passed": not failed_checks,
        "check_score": round(passed_checks / total_checks, 4),
        "checks": checks,
        "failed_checks": failed_checks,
        "fallback_used": fallback_used,
        "validation_passed": validation_passed,
        "repair_triggered": bool(repair_events),
        "repair_rounds": len(repair_events),
        "repair_agent_succeeded": bool(repair_events)
        and all(item.get("status") == "success" for item in repair_events),
        "repair_succeeded": bool(repair_events)
        and any(item.get("effective") is True for item in repair_events)
        and validation_passed,
        "retrieval_successes": sum(item.get("status") == "success" for item in retrieval_events),
        "retrieval_steps": len(retrieval_events),
        "local_knowledge": {
            "status": (knowledge_event or {}).get("status", "missing"),
            "source_count": int(knowledge_metrics.get("source_count", 0) or 0),
            "total_claims": int(knowledge_metrics.get("total_claims", 0) or 0),
            "supported_claims": int(knowledge_metrics.get("supported_claims", 0) or 0),
            "unsupported_claims": unsupported_claims,
            "unverified_claims": int(knowledge_metrics.get("unverified_claims", 0) or 0),
            "claim_parse_error": claim_parse_error,
        },
        "retried_steps": sum(
            1 for item in trace
            if item.get("agent") != "Orchestrator" and int(item.get("attempts", 1) or 1) > 1
        ),
        "latency_ms": float(orchestrator_event.get("duration_ms", 0)) if orchestrator_event else 0.0,
        "trace": trace,
        "plan_summary": {
            "city": plan.city,
            "days": len(plan.days),
            "weather_days": len(plan.weather_info),
            "budget_total": plan.budget.total if plan.budget else None,
        },
    }


def aggregate_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_cases = len(results)
    passed_cases = sum(result["passed"] for result in results)
    fallback_cases = sum(result["fallback_used"] for result in results)
    validation_passes = sum(result["validation_passed"] for result in results)
    repair_cases = sum(result["repair_triggered"] for result in results)
    repair_successes = sum(result["repair_succeeded"] for result in results)
    latencies = [result["latency_ms"] for result in results]

    retrieval_steps = sum(result["retrieval_steps"] for result in results)
    retrieval_successes = sum(result["retrieval_successes"] for result in results)
    total_agent_steps = sum(
        sum(1 for event in result["trace"] if event.get("agent") != "Orchestrator")
        for result in results
    )
    retried_steps = sum(result["retried_steps"] for result in results)

    planner_successes = sum(
        any(event.get("agent") == "Planner Agent" and event.get("status") == "success" for event in result["trace"])
        for result in results
    )

    knowledge_cases = [result["local_knowledge"] for result in results]
    knowledge_source_covered = sum(item["source_count"] > 0 for item in knowledge_cases)
    knowledge_degraded = sum(item["status"] in {"fallback", "failed", "missing"} for item in knowledge_cases)
    knowledge_schema_valid = sum(not item["claim_parse_error"] for item in knowledge_cases)
    total_claims = sum(item["total_claims"] for item in knowledge_cases)
    supported_claims = sum(item["supported_claims"] for item in knowledge_cases)
    unsupported_claims = sum(item["unsupported_claims"] for item in knowledge_cases)
    unverified_claims = sum(item["unverified_claims"] for item in knowledge_cases)

    error_categories: Counter[str] = Counter()
    for result in results:
        for event in result["trace"]:
            category = event.get("error_category")
            if category:
                error_categories[str(category)] += 1
            for retry_error in event.get("retry_errors", []) or []:
                retry_category = retry_error.get("category")
                if retry_category:
                    error_categories[f"retry:{retry_category}"] += 1

    return {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "case_pass_rate": _percentage(passed_cases, total_cases),
        "average_check_score": round(sum(result["check_score"] for result in results) / total_cases, 4) if total_cases else 0.0,
        "structured_output_success_rate": _percentage(planner_successes, total_cases),
        "retrieval_agent_success_rate": _percentage(retrieval_successes, retrieval_steps),
        "local_knowledge_source_coverage_rate": _percentage(knowledge_source_covered, total_cases),
        "local_knowledge_degraded_rate": _percentage(knowledge_degraded, total_cases),
        "local_knowledge_schema_valid_rate": _percentage(knowledge_schema_valid, total_cases),
        "local_knowledge_supported_claim_rate": _percentage(supported_claims, total_claims),
        "local_knowledge_unsupported_claim_rate": _percentage(unsupported_claims, total_claims),
        "local_knowledge_unverified_claim_rate": _percentage(unverified_claims, total_claims),
        "local_knowledge_claims": {
            "total": total_claims,
            "supported": supported_claims,
            "unsupported": unsupported_claims,
            "unverified": unverified_claims,
        },
        "validation_pass_rate": _percentage(validation_passes, total_cases),
        "repair_trigger_rate": _percentage(repair_cases, total_cases),
        "repair_success_rate": _percentage(repair_successes, repair_cases),
        "fallback_rate": _percentage(fallback_cases, total_cases),
        "agent_step_retry_rate": _percentage(retried_steps, total_agent_steps),
        "average_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": _p95(latencies),
        "error_categories": dict(error_categories),
    }


def print_summary(metrics: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    print("\n=== Multi-Agent Trip Planner Eval ===")
    print(f"Cases: {metrics['passed_cases']}/{metrics['total_cases']} passed")
    print(f"Case pass rate:                    {metrics['case_pass_rate']:.2%}")
    print(f"Average check score:               {metrics['average_check_score']:.2%}")
    print(f"Structured output success:         {metrics['structured_output_success_rate']:.2%}")
    print(f"Retrieval agent success:           {metrics['retrieval_agent_success_rate']:.2%}")
    print(f"Local Knowledge source coverage:   {metrics['local_knowledge_source_coverage_rate']:.2%}")
    print(f"Local Knowledge degraded rate:     {metrics['local_knowledge_degraded_rate']:.2%}")
    print(f"Local Knowledge schema valid:      {metrics['local_knowledge_schema_valid_rate']:.2%}")
    print(f"Local Knowledge supported claims:  {metrics['local_knowledge_supported_claim_rate']:.2%}")
    print(f"Local Knowledge unsupported claims:{metrics['local_knowledge_unsupported_claim_rate']:.2%}")
    print(f"Validation pass rate:              {metrics['validation_pass_rate']:.2%}")
    print(f"Repair trigger rate:               {metrics['repair_trigger_rate']:.2%}")
    print(f"Repair success rate:               {metrics['repair_success_rate']:.2%}")
    print(f"Fallback rate:                     {metrics['fallback_rate']:.2%}")
    print(f"Agent step retry rate:             {metrics['agent_step_retry_rate']:.2%}")
    print(f"Average latency:                   {metrics['average_latency_ms']:.2f} ms")
    print(f"P95 latency:                       {metrics['p95_latency_ms']:.2f} ms")

    print("\nCase details:")
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        failures = ", ".join(result["failed_checks"]) or "-"
        knowledge = result["local_knowledge"]
        print(
            f"  [{status}] {result['id']}: score={result['check_score']:.2%}, "
            f"latency={result['latency_ms']:.0f}ms, repair={result['repair_triggered']}, "
            f"knowledge_sources={knowledge['source_count']}, unsupported={knowledge['unsupported_claims']}, "
            f"failed={failures}"
        )


def select_cases(
    cases: Iterable[Dict[str, Any]],
    selected_ids: List[str] | None,
    limit: int | None,
) -> List[Dict[str, Any]]:
    selected = list(cases)
    if selected_ids:
        wanted = set(selected_ids)
        selected = [case for case in selected if case.get("id") in wanted]
        missing = wanted - {case.get("id") for case in selected}
        if missing:
            raise ValueError(f"unknown eval case(s): {', '.join(sorted(missing))}")
    if limit is not None:
        selected = selected[:limit]
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic travel-agent evaluations")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.0,
        help="Exit non-zero when case_pass_rate is below this value (0.0-1.0).",
    )
    args = parser.parse_args()

    if not 0.0 <= args.min_pass_rate <= 1.0:
        parser.error("--min-pass-rate must be between 0.0 and 1.0")

    cases = select_cases(load_cases(args.cases), args.case_ids, args.limit)
    if not cases:
        print("No eval cases selected.", file=sys.stderr)
        return 2

    planner = get_trip_planner_agent()
    results: List[Dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['id']} - {case['city']}")
        request = build_request(case)
        plan, trace = execute_trip_plan(planner, request)
        results.append(evaluate_case(case, request, plan, trace))

    metrics = aggregate_results(results)
    report = {
        "evaluation": "multi-agent-trip-planner-deterministic-v3",
        "metrics": metrics,
        "results": results,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print_summary(metrics, results)
    print(f"\nReport: {args.output}")

    if metrics["case_pass_rate"] < args.min_pass_rate:
        print(
            f"Eval gate failed: {metrics['case_pass_rate']:.2%} < {args.min_pass_rate:.2%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
