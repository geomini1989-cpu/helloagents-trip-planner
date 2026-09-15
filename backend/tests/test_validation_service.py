from app.models.schemas import TripPlan, TripRequest
from app.services.constraint_service import merge_constraints_from_text
from app.services.route_service import parse_route_metrics
from app.services.validation_service import validate_trip_plan


def test_natural_language_constraints_are_structured():
    request = TripRequest(
        city="广州",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travel_days=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input="总预算控制在1800元以内，每天最多2个景点，单段交通不要超过45分钟，每天游览最多6小时",
    )

    merged = merge_constraints_from_text(request)

    assert merged.constraints.max_budget == 1800
    assert merged.constraints.max_daily_attractions == 2
    assert merged.constraints.max_route_minutes == 45
    assert merged.constraints.max_daily_visit_minutes == 360


def test_route_parser_supports_json_metrics():
    result = parse_route_metrics('{"route":{"distance":3200,"duration":900}}')
    assert result["distance_meters"] == 3200
    assert result["duration_seconds"] == 900


def test_validator_detects_budget_and_route_conflicts_without_external_tool():
    request = TripRequest(
        city="北京",
        start_date="2026-10-01",
        end_date="2026-10-01",
        travel_days=1,
        transportation="步行",
        accommodation="经济型酒店",
        constraints={
            "max_budget": 500,
            "max_daily_attractions": 3,
            "max_daily_visit_minutes": 480,
            "max_route_minutes": 30,
        },
    )
    plan = TripPlan(**{
        "city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-01",
        "days": [{
            "date": "2026-10-01",
            "day_index": 0,
            "description": "test",
            "transportation": "步行",
            "accommodation": "经济型酒店",
            "attractions": [
                {
                    "name": "A",
                    "address": "A",
                    "location": {"longitude": 116.397, "latitude": 39.916},
                    "visit_duration": 120,
                    "description": "A",
                },
                {
                    "name": "B",
                    "address": "B",
                    "location": {"longitude": 116.497, "latitude": 39.916},
                    "visit_duration": 120,
                    "description": "B",
                },
            ],
            "meals": [
                {"type": "breakfast", "name": "早餐"},
                {"type": "lunch", "name": "午餐"},
                {"type": "dinner", "name": "晚餐"},
            ],
        }],
        "weather_info": [],
        "overall_suggestions": "test",
        "budget": {
            "total_attractions": 100,
            "total_hotels": 300,
            "total_meals": 200,
            "total_transportation": 50,
            "total": 650,
        },
    })

    report = validate_trip_plan(plan, request, check_routes=False)
    codes = {issue.code for issue in report.issues}

    assert report.passed is False
    assert "budget_exceeded" in codes
    assert "route_leg_too_long" in codes
    assert report.route_source_counts["coordinate_estimate"] == 1
