import json

from app.services.local_knowledge_service import (
    LocalKnowledgeResult,
    LocalKnowledgeSource,
    normalize_agent_claims,
    search_local_knowledge,
)


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


def test_placeholder_api_key_also_degrades_without_calling_provider():
    def opener(*args, **kwargs):
        raise AssertionError("provider should not be called with a placeholder key")

    result = search_local_knowledge(
        city="北京",
        attraction_context="故宫",
        start_date="2026-10-01",
        end_date="2026-10-03",
        api_key="your_tavily_api_key_here",
        opener=opener,
    )

    assert result.degraded is True
    assert result.error == "TAVILY_API_KEY not configured"


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


def _knowledge_result():
    return LocalKnowledgeResult(
        query="故宫 预约",
        sources=[
            LocalKnowledgeSource(
                title="故宫博物院参观须知",
                url="https://example.org/official",
                content="参观须预约",
                score=0.99,
            )
        ],
    )


def test_verified_claim_must_reference_current_search_source():
    result = _knowledge_result()
    context = normalize_agent_claims(
        json.dumps({
            "claims": [
                {
                    "attraction": "故宫",
                    "claim_type": "reservation",
                    "claim": "参观需要提前预约",
                    "verification_status": "verified",
                    "source_url": "https://example.org/official",
                }
            ],
            "notes": [],
        }, ensure_ascii=False),
        result,
    )

    assert result.claim_parse_error is None
    assert len(result.supported_claims) == 1
    assert result.unsupported_claims == []
    assert result.claim_metrics()["supported_claim_rate"] == 1.0
    parsed_context = json.loads(context)
    assert parsed_context["verified_claims"][0]["source_url"] == "https://example.org/official"


def test_invented_source_url_is_marked_unsupported_and_filtered_from_planner_context():
    result = _knowledge_result()
    context = normalize_agent_claims(
        json.dumps({
            "claims": [
                {
                    "attraction": "故宫",
                    "claim_type": "opening_hours",
                    "claim": "每天凌晨开放",
                    "verification_status": "verified",
                    "source_url": "https://hallucinated.example/fake",
                }
            ]
        }, ensure_ascii=False),
        result,
    )

    assert result.supported_claims == []
    assert len(result.unsupported_claims) == 1
    assert result.claim_metrics()["unsupported_claim_rate"] == 1.0
    parsed_context = json.loads(context)
    assert parsed_context["verified_claims"] == []


def test_invalid_agent_json_becomes_parse_error_with_no_trusted_claims():
    result = _knowledge_result()
    context = normalize_agent_claims("not-json", result)

    assert result.claim_parse_error
    assert result.claims == []
    assert json.loads(context)["verified_claims"] == []
