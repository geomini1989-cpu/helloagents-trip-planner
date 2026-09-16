"""Coordinator routing evaluation without executing the full trip workflow.

This benchmark measures whether the Coordinator selects the correct intent and the
smallest backend-owned canonical capability graph. It intentionally does not call AMap,
Tavily, GIS or Planner, so routing quality is isolated from downstream failures.

Run from backend/:
    python -m evals.run_routing_eval
    python -m evals.run_routing_eval --limit 6
    python -m evals.run_routing_eval --case route-reorder
    python -m evals.run_routing_eval --min-intent-accuracy 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.services.coordinator_service import ExecutionPlan, build_execution_plan


EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = EVAL_DIR / "routing_cases.json"
DEFAULT_REPORT = EVAL_DIR / "reports" / "routing-latest.json"
FULL_PLAN_CAPABILITY_COUNT = 8
AGENT_CAPABILITIES = {
    "attraction",
    "weather",
    "hotel",
    "local_knowledge",
    "planner",
    "revision",
    "repair",
}


def _percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def load_cases(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError("routing eval cases must be a JSON array")
    return data


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
            raise ValueError(f"unknown routing eval case(s): {', '.join(sorted(missing))}")
    if limit is not None:
        selected = selected[:limit]
    return selected


def evaluate_routing_case(case: Dict[str, Any], plan: ExecutionPlan) -> Dict[str, Any]:
    expected_intent = str(case["expected_intent"])
    expected_capabilities = list(case["expected_capabilities"])
    selected_capabilities = list(plan.capabilities)

    expected_set = set(expected_capabilities)
    selected_set = set(selected_capabilities)
    extra = [item for item in selected_capabilities if item not in expected_set]
    missing = [item for item in expected_capabilities if item not in selected_set]
    extra_agent_capabilities = [item for item in extra if item in AGENT_CAPABILITIES]

    intent_correct = plan.intent == expected_intent
    capabilities_exact = selected_capabilities == expected_capabilities
    graph_minimal = not extra and not missing

    return {
        "id": case["id"],
        "message": case["message"],
        "has_session": bool(case["has_session"]),
        "expected_intent": expected_intent,
        "actual_intent": plan.intent,
        "intent_correct": intent_correct,
        "expected_capabilities": expected_capabilities,
        "selected_capabilities": selected_capabilities,
        "capabilities_exact": capabilities_exact,
        "graph_minimal": graph_minimal,
        "extra_capabilities": extra,
        "missing_capabilities": missing,
        "unnecessary_agent_capabilities": extra_agent_capabilities,
        "source": plan.source,
        "reason": plan.reason,
        "selected_capability_count": len(selected_capabilities),
        "expected_capability_count": len(expected_capabilities),
        "capability_reduction_vs_full_plan": round(
            max(0, FULL_PLAN_CAPABILITY_COUNT - len(selected_capabilities)) / FULL_PLAN_CAPABILITY_COUNT,
            4,
        ),
        "passed": intent_correct and capabilities_exact,
    }


def aggregate_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    intent_correct = sum(item["intent_correct"] for item in results)
    exact = sum(item["capabilities_exact"] for item in results)
    minimal = sum(item["graph_minimal"] for item in results)
    fallback = sum(item["source"] != "llm" for item in results)

    selected_total = sum(item["selected_capability_count"] for item in results)
    expected_total = sum(item["expected_capability_count"] for item in results)
    extra_total = sum(len(item["extra_capabilities"]) for item in results)
    missing_total = sum(len(item["missing_capabilities"]) for item in results)
    unnecessary_agent_total = sum(len(item["unnecessary_agent_capabilities"]) for item in results)
    selected_agent_total = sum(
        sum(capability in AGENT_CAPABILITIES for capability in item["selected_capabilities"])
        for item in results
    )

    sources = Counter(item["source"] for item in results)
    confusion = Counter(
        f"{item['expected_intent']}->{item['actual_intent']}"
        for item in results
        if not item["intent_correct"]
    )

    return {
        "total_cases": total,
        "passed_cases": sum(item["passed"] for item in results),
        "intent_accuracy": _percentage(intent_correct, total),
        "capability_exact_match_rate": _percentage(exact, total),
        "minimal_graph_rate": _percentage(minimal, total),
        "fallback_router_rate": _percentage(fallback, total),
        "unnecessary_capability_rate": _percentage(extra_total, selected_total),
        "missing_capability_rate": _percentage(missing_total, expected_total),
        "unnecessary_agent_call_rate": _percentage(unnecessary_agent_total, selected_agent_total),
        "average_selected_capabilities": round(selected_total / total, 2) if total else 0.0,
        "average_expected_capabilities": round(expected_total / total, 2) if total else 0.0,
        "average_capability_reduction_vs_full_plan": round(
            sum(item["capability_reduction_vs_full_plan"] for item in results) / total,
            4,
        ) if total else 0.0,
        "router_sources": dict(sources),
        "intent_confusion": dict(confusion),
    }


def print_summary(metrics: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    print("\n=== Coordinator Routing Eval ===")
    print(f"Cases: {metrics['passed_cases']}/{metrics['total_cases']} exact-pass")
    print(f"Intent accuracy:                    {metrics['intent_accuracy']:.2%}")
    print(f"Capability exact match:             {metrics['capability_exact_match_rate']:.2%}")
    print(f"Minimal graph rate:                 {metrics['minimal_graph_rate']:.2%}")
    print(f"Fallback router rate:               {metrics['fallback_router_rate']:.2%}")
    print(f"Unnecessary capability rate:        {metrics['unnecessary_capability_rate']:.2%}")
    print(f"Unnecessary Agent call rate:        {metrics['unnecessary_agent_call_rate']:.2%}")
    print(f"Missing capability rate:            {metrics['missing_capability_rate']:.2%}")
    print(f"Avg capabilities selected:          {metrics['average_selected_capabilities']:.2f}")
    print(f"Avg reduction vs full-plan graph:   {metrics['average_capability_reduction_vs_full_plan']:.2%}")

    print("\nCase details:")
    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        print(
            f"  [{status}] {item['id']}: expected={item['expected_intent']}, "
            f"actual={item['actual_intent']}, source={item['source']}, "
            f"caps={' → '.join(item['selected_capabilities'])}"
        )
        if item["extra_capabilities"] or item["missing_capabilities"]:
            print(
                f"         extra={item['extra_capabilities'] or '-'}, "
                f"missing={item['missing_capabilities'] or '-'}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Coordinator routing quality")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--min-intent-accuracy",
        type=float,
        default=0.0,
        help="Exit non-zero when intent_accuracy is below this value (0.0-1.0).",
    )
    args = parser.parse_args()

    if not 0.0 <= args.min_intent_accuracy <= 1.0:
        parser.error("--min-intent-accuracy must be between 0.0 and 1.0")

    cases = select_cases(load_cases(args.cases), args.case_ids, args.limit)
    if not cases:
        print("No routing eval cases selected.", file=sys.stderr)
        return 2

    results: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['id']}")
        plan = build_execution_plan(
            str(case["message"]),
            has_session=bool(case["has_session"]),
        )
        results.append(evaluate_routing_case(case, plan))

    metrics = aggregate_results(results)
    report = {
        "evaluation": "multi-agent-trip-planner-routing-v1",
        "metrics": metrics,
        "results": results,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print_summary(metrics, results)
    print(f"\nReport: {args.output}")

    if metrics["intent_accuracy"] < args.min_intent_accuracy:
        print(
            f"Routing eval gate failed: {metrics['intent_accuracy']:.2%} < {args.min_intent_accuracy:.2%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
