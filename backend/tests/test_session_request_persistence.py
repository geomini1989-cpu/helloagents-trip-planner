from app.services import session_service


def test_session_persists_original_request(tmp_path, monkeypatch):
    monkeypatch.setattr(session_service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(session_service, "DB_PATH", tmp_path / "trip_planner.db")
    session_service._init_db()

    plan = {
        "city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "days": [],
        "weather_info": [],
        "overall_suggestions": "test",
        "budget": None,
    }
    request = {
        "city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "travel_days": 3,
        "transportation": "公共交通",
        "accommodation": "舒适型酒店",
        "preferences": ["历史文化"],
        "free_text_input": "预算 2500 元以内",
        "constraints": {
            "max_budget": 2500,
            "max_daily_attractions": 3,
            "max_daily_visit_minutes": 480,
            "max_route_minutes": 60,
        },
    }

    session_id = session_service.create_session(plan, request=request)
    restored = session_service.get_session(session_id)

    assert restored is not None
    assert restored["request"] == request
    assert restored["request"]["constraints"]["max_budget"] == 2500
