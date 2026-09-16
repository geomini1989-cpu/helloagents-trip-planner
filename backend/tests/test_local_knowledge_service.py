import json

from app.services.local_knowledge_service import search_local_knowledge


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_missing_api_key_degrades_without_calling_provider():
    called = False

    def opener(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider should not be called without a key")

    result = search_local_knowledge(
        city="北京",
        attraction_context="故宫；天坛",
        start_date="2026-10-01",
        end_date="2026-10-03",
        api_key="",
        opener=opener,
    )

    assert result.degraded is True
    assert result.error == "TAVILY_API_KEY not configured"
    assert result.sources == []
    assert called is False
    assert "不要猜测" in result.as_agent_context()


def test_provider_results_are_normalized_into_traceable_sources():
    captured = {}

    def opener(request, timeout):
        captured["timeout"] = timeout
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({
            "results": [
                {
                    "title": "故宫博物院参观须知",
                    "url": "https://example.org/palace-notice",
                    "content": "参观须提前预约，开放安排以官方公告为准。",
                    "score": 0.92,
                },
                {
                    "title": "invalid result without url",
                    "url": "",
                    "content": "should be ignored",
                },
            ]
        })

    result = search_local_knowledge(
        city="北京",
        attraction_context="故宫",
        start_date="2026-10-01",
        end_date="2026-10-03",
        api_key="test-key",
        max_results=3,
        opener=opener,
    )

    assert result.degraded is False
    assert len(result.sources) == 1
    assert result.sources[0].title == "故宫博物院参观须知"
    assert result.sources[0].url == "https://example.org/palace-notice"
    assert result.sources[0].score == 0.92
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["max_results"] == 3
    assert "故宫" in captured["payload"]["query"]
    assert "URL: https://example.org/palace-notice" in result.as_agent_context()
