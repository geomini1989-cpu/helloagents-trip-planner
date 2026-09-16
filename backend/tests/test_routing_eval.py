from app.services.coordinator_service import ExecutionNode, ExecutionPlan
from evals.run_routing_eval import aggregate_results, evaluate_routing_case


def _case():
    return {
        "id": "route-only",
        "message": "只优化路线",
        "has_session": True,
        "expected_intent": "route_optimize",
        "expected_capabilities": ["gis", "validator"],
    }


def test_routing_eval_exact_graph_passes():
    plan = ExecutionPlan(
        intent="route_optimize",
        nodes=[
            ExecutionNode("gis", "gis", []),
            ExecutionNode("validator", "validator", ["gis"]),
        ],
        source="llm",
        reason="route only",
    )
    result = evaluate_routing_case(_case(), plan)

    assert result["passed"] is True
    assert result["extra_capabilities"] == []
    assert result["missing_capabilities"] == []
    assert result["capability_reduction_vs_full_plan"] == 0.75


def test_routing_eval_detects_unnecessary_agent_call():
    plan = ExecutionPlan(
        intent="route_optimize",
        nodes=[
            ExecutionNode("hotel", "hotel", []),
            ExecutionNode("gis", "gis", []),
            ExecutionNode("validator", "validator", ["gis"]),
        ],
        source="llm",
        reason="over-routed",
    )
    result = evaluate_routing_case(_case(), plan)
    metrics = aggregate_results([result])

    assert result["passed"] is False
    assert result["extra_capabilities"] == ["hotel"]
    assert result["unnecessary_agent_capabilities"] == ["hotel"]
    assert metrics["intent_accuracy"] == 1.0
    assert metrics["minimal_graph_rate"] == 0.0
    assert metrics["unnecessary_agent_call_rate"] == 1.0
