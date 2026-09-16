from app.models.schemas import RevisionLocks, TripPlan
from app.services.coordinator_service import validate_coordinator_output
from app.services.dynamic_orchestration_service import execute_session_task
from app.services.local_knowledge_service import LocalKnowledgeResult, LocalKnowledgeSource
from app.services.validation_service import ValidationReport


class FakeRouteReport:
    def to_dict(self):
        return {
            "optimized_days": 1,
            "reordered_days": 0,
            "saved_minutes": 0,
            "source_counts": {"haversine_estimate": 1},
            "days": [],
        }


class FakePlanner:
    def __init__(self):
        self.revision_feedback = ""

    def research_local_knowledge(self, request, attraction_context):
        assert "故宫" in attraction_context
        return (
            "故宫需按官方规则预约。来源：https://example.org/official",
            LocalKnowledgeResult(
                query="故宫 预约",
                sources=[
                    LocalKnowledgeSource(
                        title="官方参观须知",
                        url="https://example.org/official",
                        content="参观须预约",
                        score=0.99,
                    )
                ],
            ),
        )

    def revise_trip(self, current_plan, user_feedback, locks):
        self.revision_feedback = user_feedback
        return current_plan


def _session():
    return {
        "request": {
            "city": "北京",
            "start_date": "2026-10-01",
            "end_date": "2026-10-01",
            "travel_days": 1,
            "transportation": "公共交通",
            "accommodation": "经济型酒店",
            "preferences": ["历史文化"],
            "free_text_input": "",
            "constraints": {
                "max_budget": 2500,
                "max_daily_attractions": 3,
                "max_daily_visit_minutes": 480,
                "max_route_minutes": 60,
            },
        },
        "current_plan": {
            "city": "北京",
            "start_date": "2026-10-01",
            "end_date": "2026-10-01",
            "days": [
                {
                    "date": "2026-10-01",
                    "day_index": 0,
                    "description": "故宫游览",
                    "transportation": "公共交通",
                    "accommodation": "经济型酒店",
                    "hotel": None,
                    "attractions": [
                        {
                            "name": "故宫",
                            "address": "北京市东城区景山前街4号",
                            "location": {"longitude": 116.397, "latitude": 39.916},
                            "visit_duration": 180,
                            "description": "历史文化景点",
                            "category": "博物馆",
                            "ticket_price": 60,
                        }
                    ],
                    "meals": [],
                }
            ],
            "weather_info": [],
            "overall_suggestions": "测试计划",
            "budget": {
                "total_attractions": 60,
                "total_hotels": 0,
                "total_meals": 0,
                "total_transportation": 0,
                "total": 60,
            },
        },
    }


def test_poi_rules_check_injects_verified_context_into_revision(monkeypatch):
    planner = FakePlanner()
    plan = validate_coordinator_output(
        {"intent": "poi_rules_check", "reason": "核验预约规则"},
        has_session=True,
    )

    monkeypatch.setattr(
        "app.services.dynamic_orchestration_service.optimize_trip_routes",
        lambda trip_plan, request: (trip_plan, FakeRouteReport()),
    )
    monkeypatch.setattr(
        "app.services.dynamic_orchestration_service.validate_trip_plan",
        lambda trip_plan, request, check_routes=True: ValidationReport(passed=True, issues=[]),
    )

    result, trace, degraded = execute_session_task(
        planner,
        _session(),
        "故宫需要预约吗？如果需要就保留提醒",
        RevisionLocks(),
        plan,
    )

    assert TripPlan(**result).city == "北京"
    assert "https://example.org/official" in planner.revision_feedback
    assert "仅把有来源且已核验的信息视为事实" in planner.revision_feedback
    assert [item["agent"] for item in trace][:3] == [
        "Coordinator",
        "Local Knowledge Agent",
        "Revision Agent",
    ]
    knowledge_event = next(item for item in trace if item["agent"] == "Local Knowledge Agent")
    assert knowledge_event["knowledge_provider"] == "tavily"
    assert knowledge_event["knowledge_sources"][0]["url"] == "https://example.org/official"
    assert degraded is False
