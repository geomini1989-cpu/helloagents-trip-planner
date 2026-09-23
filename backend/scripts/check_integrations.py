"""Live integration check for AMap MCP and Tavily.

Run from backend/:
    python -m scripts.check_integrations

The script never prints secret values. It performs one MCP weather call and, when
Tavily is configured, one lightweight web-search request.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.agents.trip_planner_agent import get_trip_planner_agent
from app.config import get_settings, is_configured_secret
from app.services.local_knowledge_service import search_local_knowledge
from app.services.resilience_service import ensure_successful_tool_result


def main() -> int:
    settings = get_settings()

    print("=== Integration Check ===")
    print(f"AMap Web Service Key: {'configured' if is_configured_secret(settings.amap_api_key) else 'missing'}")
    print(f"Tavily API Key:       {'configured' if is_configured_secret(settings.tavily_api_key) else 'missing'}")

    if not is_configured_secret(settings.amap_api_key):
        print("\n[FAIL] AMAP_API_KEY is not configured.")
        return 1

    planner = get_trip_planner_agent()

    print("\n--- AMap MCP tools ---")
    tool_list = planner.amap_tool.run({"action": "list_tools"})
    print(tool_list[:3000])
    ensure_successful_tool_result(tool_list, context="AMap/list_tools")

    required = ("maps_text_search", "maps_weather")
    missing = [name for name in required if name not in str(tool_list)]
    if missing:
        print(f"[FAIL] Missing expected AMap MCP tools: {', '.join(missing)}")
        return 2

    print("\n--- AMap weather smoke test ---")
    weather = planner.amap_tool.run({
        "action": "call_tool",
        "tool_name": "maps_weather",
        "arguments": {"city": "北京"},
    })
    ensure_successful_tool_result(weather, context="AMap/maps_weather")
    print(str(weather)[:1600])
    print("[PASS] AMap MCP weather call succeeded.")

    if not is_configured_secret(settings.tavily_api_key):
        print("\n[SKIP] Tavily is not configured. Add TAVILY_API_KEY to backend/.env.")
        return 0

    print("\n--- Tavily Local Knowledge smoke test ---")
    start = date.today() + timedelta(days=1)
    result = search_local_knowledge(
        city="北京",
        attraction_context="故宫博物院",
        start_date=start.isoformat(),
        end_date=start.isoformat(),
        max_results=3,
    )
    if result.degraded:
        print(f"[FAIL] Tavily degraded: {result.error}")
        return 3

    print(f"Sources: {len(result.sources)}")
    for source in result.sources[:3]:
        print(f"- {source.title}: {source.url}")
    print("[PASS] Tavily search succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
