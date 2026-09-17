from app.models.schemas import Attraction, RevisionLocks
from app.services.gis_optimizer_service import optimize_day_attractions
from app.services.revision_lock_service import enforce_revision_locks, merge_revision_locks


class FakeRouteProvider:
    def __init__(self):
        self.route_types = []
        self.minutes = {
            ("A", "B"): 60,
            ("B", "C"): 60,
            ("A", "C"): 10,
            ("C", "B"): 10,
            ("B", "A"): 50,
            ("C", "A"): 50,
        }

    def plan_route(self, origin_address, destination_address, **kwargs):
        self.route_types.append(kwargs.get("route_type"))
        minutes = self.minutes[(origin_address, destination_address)]
        return {
            "duration_seconds": minutes * 60,
            "distance_meters": minutes * 500,
        }


def _attraction(name: str, longitude: float) -> Attraction:
    return Attraction(
        name=name,
        address=name,
        location={"longitude": longitude, "latitude": 23.1},
        visit_duration=90,
        description=name,
    )


def test_gis_optimizer_reorders_by_network_cost():
    attractions = [
        _attraction("A", 113.1),
        _attraction("B", 113.2),
        _attraction("C", 113.3),
    ]

    provider = FakeRouteProvider()
    optimized, report = optimize_day_attractions(
        attractions,
        day_index=0,
        city="广州",
        transportation="步行+公共交通",
        route_provider=provider,
    )

    assert [item.name for item in optimized] == ["A", "C", "B"]
    assert report.reordered is True
    assert report.before_minutes == 120
    assert report.after_minutes == 20
    assert report.saved_minutes == 100
    assert report.source_counts["amap_network"] == 6
    assert set(provider.route_types) == {"transit"}


def test_natural_language_revision_locks_are_persistent_rules():
    locks = merge_revision_locks(
        RevisionLocks(),
        "第一天和酒店已经确定，不要改，把第三天改轻松一点",
    )

    assert locks.locked_day_indexes == [0]
    assert locks.lock_all_hotels is True


def test_lock_guard_restores_locked_day_and_hotels():
    original = {
        "city": "广州",
        "days": [
            {
                "day_index": 0,
                "description": "原第一天",
                "accommodation": "酒店A",
                "hotel": {"name": "酒店A"},
                "attractions": [{"name": "景点A"}],
            },
            {
                "day_index": 1,
                "description": "原第二天",
                "accommodation": "酒店B",
                "hotel": {"name": "酒店B"},
                "attractions": [{"name": "景点B"}],
            },
        ],
    }
    revised = {
        "city": "广州",
        "days": [
            {
                "day_index": 0,
                "description": "Agent 偷偷改了第一天",
                "accommodation": "新酒店",
                "hotel": {"name": "新酒店"},
                "attractions": [{"name": "新景点"}],
            },
            {
                "day_index": 1,
                "description": "第二天允许修改",
                "accommodation": "另一个酒店",
                "hotel": {"name": "另一个酒店"},
                "attractions": [{"name": "景点B"}],
            },
        ],
    }
    locks = RevisionLocks(locked_day_indexes=[0], lock_all_hotels=True)

    protected, violations = enforce_revision_locks(original, revised, locks)

    assert protected["days"][0] == original["days"][0]
    assert protected["days"][1]["description"] == "第二天允许修改"
    assert protected["days"][1]["hotel"] == original["days"][1]["hotel"]
    assert protected["days"][1]["accommodation"] == original["days"][1]["accommodation"]
    assert {item["type"] for item in violations} == {"locked_day_changed", "locked_hotel_changed"}
