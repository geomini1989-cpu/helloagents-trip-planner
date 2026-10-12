from app.services import session_service
import pytest
from app.services.plan_comparison_service import compare_plans
from app.services.revision_scope_service import enforce_revision_scope, targeted_day_indexes


def _plan():
    return {
        "city": "成都", "start_date": "2026-10-01", "end_date": "2026-10-03",
        "days": [
            {"day_index": i, "date": f"2026-10-0{i + 1}",
             "description": f"原第{i + 1}天", "attractions": [{"name": f"景点{i + 1}"}]}
            for i in range(3)
        ],
        "budget": {"total": 1000}, "overall_suggestions": "保持轻松",
        "weather_info": [{"date": f"2026-10-0{i + 1}", "day_weather": "晴"} for i in range(3)],
    }


def test_day_scoped_revision_restores_unrelated_days():
    original = _plan()
    candidate = _plan()
    candidate["days"][0]["description"] = "模型误改"
    candidate["days"][1]["attractions"] = [{"name": "熊猫基地"}]
    candidate["days"][1]["date"] = "2026-11-01"
    candidate["weather_info"][0]["day_weather"] = "模型误改天气"
    targets = targeted_day_indexes("把第二天改成熊猫基地", original)
    protected, violations = enforce_revision_scope(original, candidate, targets)
    assert targets == {1}
    assert protected["days"][0] == original["days"][0]
    assert protected["weather_info"][0] == original["weather_info"][0]
    assert protected["days"][1]["attractions"][0]["name"] == "熊猫基地"
    assert protected["days"][1]["date"] == original["days"][1]["date"]
    assert violations[0]["type"] == "out_of_scope_day_changed"


def test_locked_mention_is_not_change_target():
    assert targeted_day_indexes("第一天不要改，第三天换成公园", _plan()) == {2}
    assert targeted_day_indexes("第一天和第二天都改成公园", _plan()) == {0, 1}
    assert targeted_day_indexes("整体节奏放慢", _plan()) == set()


def test_comparison_captures_attractions_and_budget():
    old = _plan()
    new = _plan()
    new["days"][1]["attractions"] = [{"name": "熊猫基地"}]
    new["budget"]["total"] = 1200
    new["weather_info"][1]["day_weather"] = "雨"
    comparison = compare_plans(old, new)
    assert [day["day_index"] for day in comparison["changed_days"]] == [1]
    assert comparison["changed_days"][0]["after_attractions"] == ["熊猫基地"]
    assert comparison["budget_delta"] == 200
    assert comparison["weather_changed_dates"] == ["2026-10-02"]


def test_preview_applies_once_and_refuses_stale_session(tmp_path, monkeypatch):
    monkeypatch.setattr(session_service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(session_service, "DB_PATH", tmp_path / "trip_planner.db")
    session_service._init_db()
    session_id = session_service.create_session(_plan())
    base = session_service.get_session(session_id)
    candidate = _plan()
    candidate["days"][1]["description"] = "修改后"
    first = session_service.create_preview(base, "改第二天", candidate, [], {}, {})
    stale = session_service.create_preview(base, "再次修改", candidate, [], {}, {})
    assert session_service.apply_preview(first) == "applied"
    assert session_service.apply_preview(first) == "missing"
    assert session_service.apply_preview(stale) == "stale"
    restored = session_service.get_session(session_id)
    assert restored["current_plan"]["days"][1]["description"] == "修改后"
    assert restored["history"][0]["plan"] == _plan()
    assert restored["version"] == 1
    with pytest.raises(session_service.StaleSessionError):
        session_service.update_session_plan(session_id, _plan(), expected_version=0)


def test_existing_session_database_gets_version_column(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing

    db = tmp_path / "trip_planner.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            """CREATE TABLE trip_sessions (
                session_id TEXT PRIMARY KEY, current_plan TEXT NOT NULL,
                request TEXT NOT NULL DEFAULT '{}', history TEXT NOT NULL DEFAULT '[]',
                execution_trace TEXT NOT NULL DEFAULT '[]', locks TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
    monkeypatch.setattr(session_service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(session_service, "DB_PATH", db)
    session_service._init_db()
    with closing(sqlite3.connect(db)) as conn:
        names = {row[1] for row in conn.execute("PRAGMA table_info(trip_sessions)")}
    assert "version" in names
