import pytest

from app.services.route_service import RouteService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, params))
        return FakeResponse(self.responses.pop(0))


def test_route_service_uses_coordinates_and_reuses_cached_result():
    client = FakeClient([
        {
            "status": "1",
            "route": {
                "transits": [{"distance": "3200", "duration": "900"}],
            },
        }
    ])
    service = RouteService(api_key="valid-key", http_client=client)

    kwargs = {
        "origin_city": "北京",
        "destination_city": "北京",
        "route_type": "transit",
        "origin_location": {"longitude": 116.397, "latitude": 39.916},
        "destination_location": {"longitude": 116.407, "latitude": 39.926},
    }
    first = service.plan_route("A", "B", **kwargs)
    second = service.plan_route("A", "B", **kwargs)

    assert first["duration_seconds"] == 900
    assert first["distance_meters"] == 3200
    assert first["source"] == "amap_rest"
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(client.calls) == 1
    path, params = client.calls[0]
    assert path == "/v3/direction/transit/integrated"
    assert params["origin"] == "116.397000,39.916000"
    assert params["destination"] == "116.407000,39.926000"
    assert params["key"] == "valid-key"


def test_route_service_geocodes_only_once_per_address():
    client = FakeClient([
        {"status": "1", "geocodes": [{"location": "116.397000,39.916000"}]},
        {"status": "1", "geocodes": [{"location": "116.407000,39.926000"}]},
        {"status": "1", "route": {"paths": [{"distance": "1200", "duration": "600"}]}},
    ])
    service = RouteService(api_key="valid-key", http_client=client)

    first = service.plan_route("A", "B", origin_city="北京", destination_city="北京", route_type="walking")
    second = service.plan_route("A", "B", origin_city="北京", destination_city="北京", route_type="walking")

    assert first["duration_seconds"] == 600
    assert second["cached"] is True
    assert [path for path, _ in client.calls] == [
        "/v3/geocode/geo",
        "/v3/geocode/geo",
        "/v3/direction/walking",
    ]


def test_route_service_rejects_amap_api_errors():
    client = FakeClient([{"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}])
    service = RouteService(api_key="valid-key", http_client=client)

    with pytest.raises(RuntimeError, match="INVALID_USER_KEY"):
        service.plan_route(
            "A",
            "B",
            route_type="walking",
            origin_location={"longitude": 116.397, "latitude": 39.916},
            destination_location={"longitude": 116.407, "latitude": 39.926},
        )
