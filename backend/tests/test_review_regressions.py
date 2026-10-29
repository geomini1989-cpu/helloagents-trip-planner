import copy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import trip
from app.models.schemas import TripPlan, TripRequest, RevisionLocks
from app.services import session_service as store, validation_service, dynamic_orchestration_service as dynamic
from app.services.revision_lock_service import merge_revision_locks, enforce_revision_locks
from app.services.validation_service import validate_trip_plan
from app.services.coordinator_service import validate_coordinator_output


def plan():
    return TripPlan(
        city="北京", start_date="2026-10-01", end_date="2026-10-01",
        overall_suggestions="test",
        days=[dict(day_index=0, date="2026-10-01", description="test", transportation="步行",
                   accommodation="酒店", attractions=[
                       dict(name=name, address=name, location=dict(longitude=116.4, latitude=39.9),
                            visit_duration=60, description=name, ticket_price=cost)
                       for name, cost in [("A", 100), ("B", 50)]
                   ])],
        budget=dict(total_attractions=150, total_hotels=0, total_meals=0,
                    total_transportation=30, total=180),
    ).model_dump()


def request():
    return TripRequest(city="北京", start_date="2026-10-01", end_date="2026-10-01",
                       travel_days=1, transportation="步行", accommodation="酒店",
                       constraints={"max_budget": 3000}).model_dump()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.sqlite")
    store._init_db()
    monkeypatch.setattr(validation_service, "get_route_service", lambda: None)
    app = FastAPI()
    app.include_router(trip.router, prefix="/api")
    with TestClient(app) as client:
        sid = store.create_session(plan(), request=request())
        yield client, sid


@pytest.mark.parametrize("endpoint", ["revise", "dispatch", "preview"])
@pytest.mark.parametrize("trace", [
    [],
    [{"agent": "Trip Validator", "status": "needs_revision", "validation_report": {"passed": False}}],
    [{"agent": "Revision Agent", "status": "failed"},
     {"agent": "Trip Validator", "status": "success", "validation_report": {"passed": True}}],
])
def test_all_revision_endpoints_reject_bad_candidates(runtime, monkeypatch, endpoint, trace):
    client, sid = runtime
    original = store.get_session(sid)
    candidate = plan()
    candidate["budget"]["total"] = 9999
    monkeypatch.setattr(trip, "_execute_existing_session",
                        lambda **kwargs: (candidate, trace, RevisionLocks(), {}))
    response = client.post("/api/trip/" + endpoint,
                           json={"session_id": sid, "feedback": "test", "message": "test"})
    assert response.status_code == 422
    assert store.get_session(sid) == original
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trip_previews").fetchone()[0] == 0


@pytest.mark.parametrize("endpoint", ["revise", "dispatch"])
def test_direct_revision_persists_updated_constraints(runtime, monkeypatch, endpoint):
    client, sid = runtime
    monkeypatch.setattr(trip, "_execute_existing_session", lambda **kwargs: (
        plan(), [{"agent": "Trip Validator", "status": "success", "validation_report": {"passed": True}}],
        RevisionLocks(), {},
    ))
    response = client.post("/api/trip/" + endpoint, json={
        "session_id": sid, "feedback": "预算不超过500元", "message": "预算不超过500元",
    })
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert store.get_session(sid)["request"]["constraints"]["max_budget"] == 500


def test_preview_constraints_commit_only_on_apply(runtime, monkeypatch):
    client, sid = runtime
    monkeypatch.setattr(trip, "_execute_existing_session", lambda **kwargs: (
        plan(), [{"agent": "Trip Validator", "status": "success", "validation_report": {"passed": True}}],
        RevisionLocks(), {},
    ))
    response = client.post("/api/trip/preview", json={"session_id": sid, "feedback": "预算不超过500元"})
    assert response.status_code == 200
    assert store.get_session(sid)["request"]["constraints"]["max_budget"] == 3000
    pid = response.json()["preview_id"]
    assert store.get_preview(pid)["request"]["constraints"]["max_budget"] == 500
    applied = client.post("/api/trip/preview/" + pid + "/apply")
    assert applied.status_code == 200
    assert applied.json()["version"] == 1
    assert store.get_session(sid)["request"]["constraints"]["max_budget"] == 500
    assert client.post("/api/trip/preview/" + pid + "/apply").status_code == 404


def test_new_constraints_reach_real_validator(monkeypatch):
    original = plan()
    original["budget"]["total_transportation"] = 1350
    original["budget"]["total"] = 1500
    session = {"current_plan": original, "request": request()}
    seen = []
    class Planner:
        def revise_trip(self, current, feedback, locks):
            seen.append(feedback)
            return copy.deepcopy(current)
    class Report:
        def to_dict(self): return {}
    monkeypatch.setattr(dynamic, "optimize_trip_routes", lambda candidate, req: (candidate, Report()))
    monkeypatch.setattr(validation_service, "get_route_service", lambda: None)
    graph = validate_coordinator_output({"intent": "general_revision"}, has_session=True)
    _, trace, degraded = dynamic.execute_session_task(
        Planner(), session, "总预算改为不超过1000元", RevisionLocks(), graph)
    report = next(item["validation_report"] for item in trace if item["agent"] == "Trip Validator")
    assert not report["passed"]
    assert degraded
    assert '"max_budget": 1000' in seen[0]


def test_latest_revision_constraint_wins():
    session = {"current_plan": plan(), "request": request()}
    session["request"]["free_text_input"] = "预算不超过3000元"
    first = dynamic.request_for_revision(session, "预算不超过1000元")
    second = dynamic.request_for_revision({"request": first.model_dump()}, "预算不超过500元")
    assert second.constraints.max_budget == 500
    assert second.constraints.max_daily_attractions == 3


def test_inconsistent_budget_is_rejected_even_without_limit():
    candidate = plan()
    candidate["budget"]["total"] = 100
    req = TripRequest(**request())
    req.constraints.max_budget = None
    report = validate_trip_plan(TripPlan(**candidate), req, check_routes=False)
    assert not report.passed
    assert "budget_inconsistent" in {issue.code for issue in report.issues}


def test_restoring_locked_items_restores_their_cost():
    original = plan()
    candidate = copy.deepcopy(original)
    candidate["days"][0]["attractions"][0]["ticket_price"] = 1
    candidate["budget"]["total_attractions"] = 51
    candidate["budget"]["total"] = 81
    protected, _ = enforce_revision_locks(original, candidate, RevisionLocks(locked_day_indexes=[0]))
    assert protected["budget"]["total"] == 180


@pytest.mark.parametrize("feedback,expected", [
    ("第一天改成公园，第二天不要改", [1]),
    ("第二天不要改，第一天改成公园", [1]),
    ("第一天改成公园第二天不要改", [1]),
    ("第二天不要改第一天改成公园", [1]),
    ("锁定第一天和第二天", [0, 1]),
    ("第一天和第二天不要改", [0, 1]),
    ("第一天和酒店已经确定，不要改，把第三天改轻松一点", [0]),
])
def test_day_lock_predicates_do_not_leak(feedback, expected):
    assert merge_revision_locks({}, feedback).locked_day_indexes == expected


def test_unlock_is_local():
    locks = merge_revision_locks({"locked_day_indexes": [0, 1]}, "第一天可以改，第二天不要改")
    assert locks.locked_day_indexes == [1]


def test_manual_save_updates_server_and_invalidates_old_preview(runtime):
    client, sid = runtime
    base = store.get_session(sid)
    pid = store.create_preview(base, "test", plan(), [], {}, {}, request=request())
    changed = plan()
    changed["days"][0]["attractions"].pop()
    response = client.put("/api/trip/session/" + sid + "/plan",
                          json={"expected_version": 0, "data": changed})
    assert response.status_code == 200
    assert response.json()["version"] == 1
    saved = store.get_session(sid)
    assert len(saved["current_plan"]["days"][0]["attractions"]) == 1
    assert saved["current_plan"]["budget"]["total"] == 130
    assert client.get("/api/trip/session/" + sid).json()["version"] == 1
    assert store.apply_preview(pid) == "stale"
    assert client.put("/api/trip/session/" + sid + "/plan",
                      json={"expected_version": 0, "data": changed}).status_code == 409
    assert store.get_session(sid) == saved


def test_invalid_manual_save_preserves_current_plan(runtime):
    client, sid = runtime
    old = store.get_session(sid)
    changed = plan()
    changed["days"][0]["attractions"][0]["visit_duration"] = 1000
    response = client.put("/api/trip/session/" + sid + "/plan",
                          json={"expected_version": 0, "data": changed})
    assert response.status_code == 422
    assert store.get_session(sid) == old


def test_manual_save_respects_locks(runtime):
    client, sid = runtime
    store.update_session_locks(sid, {"locked_day_indexes": [0]})
    original = store.get_session(sid)
    changed = plan()
    changed["days"][0]["description"] = "changed"
    response = client.put("/api/trip/session/" + sid + "/plan",
                          json={"expected_version": 1, "data": changed})
    assert response.status_code == 422
    assert store.get_session(sid) == original
