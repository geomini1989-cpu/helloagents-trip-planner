import json

from app.services.coordinator_service import build_execution_plan, validate_coordinator_output


def test_weather_replan_builds_canonical_graph():
    plan = build_execution_plan(
        "明天下雨，把第二天改成室内活动，酒店别动",
        has_session=True,
        llm_runner=lambda _: json.dumps({
            "intent": "weather_replan",
            "requested_capabilities": ["weather", "revision", "gis", "validator"],
            "reason": "天气变化需要重规划",
        }),
    )

    assert plan.intent == "weather_replan"
    assert plan.capabilities == ["weather", "revision", "gis", "validator"]
    assert plan.nodes[1].depends_on == ["weather"]
    assert plan.source == "llm"


def test_llm_cannot_inject_unknown_capability_or_edges():
    plan = validate_coordinator_output(
        {
            "intent": "hotel_change",
            "requested_capabilities": ["hotel", "shell", "delete_database"],
            "nodes": [{"id": "x", "capability": "shell", "depends_on": []}],
        },
        has_session=True,
    )

    assert plan.intent == "hotel_change"
    assert plan.capabilities == ["hotel", "revision", "gis", "validator"]
    assert "shell" not in plan.capabilities
    assert "delete_database" not in plan.capabilities


def test_new_request_is_forced_to_full_plan():
    plan = validate_coordinator_output(
        {"intent": "route_optimize", "reason": "bad classification"},
        has_session=False,
    )

    assert plan.intent == "full_plan"
    assert plan.capabilities[:3] == ["attraction", "weather", "hotel"]
    assert plan.nodes[3].depends_on == ["attraction", "weather", "hotel"]


def test_session_full_plan_is_normalized_to_revision():
    plan = validate_coordinator_output(
        {"intent": "full_plan"},
        has_session=True,
    )

    assert plan.intent == "general_revision"
    assert plan.capabilities == ["revision", "gis", "validator"]


def test_invalid_llm_output_falls_back_to_deterministic_router():
    plan = build_execution_plan(
        "这三个景点怎么排最省时间？",
        has_session=True,
        llm_runner=lambda _: "not-json",
    )

    assert plan.intent == "route_optimize"
    assert plan.capabilities == ["gis", "validator"]
    assert plan.source == "heuristic_fallback"
