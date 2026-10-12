"""Deterministic, user-readable comparison of two trip plans."""

from __future__ import annotations

from typing import Any, Dict


def compare_plans(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    old_days = {int(day["day_index"]): day for day in before.get("days", [])}
    new_days = {int(day["day_index"]): day for day in after.get("days", [])}
    changes = []
    for index in sorted(set(old_days) | set(new_days)):
        old = old_days.get(index)
        new = new_days.get(index)
        if old == new:
            continue
        changes.append({
            "day_index": index,
            "date": (new or old or {}).get("date", ""),
            "before_attractions": [item.get("name", "") for item in (old or {}).get("attractions", [])],
            "after_attractions": [item.get("name", "") for item in (new or {}).get("attractions", [])],
            "hotel_changed": (old or {}).get("hotel") != (new or {}).get("hotel"),
            "meals_changed": (old or {}).get("meals") != (new or {}).get("meals"),
            "transportation_changed": (old or {}).get("transportation") != (new or {}).get("transportation"),
            "description_changed": (old or {}).get("description") != (new or {}).get("description"),
        })
    old_total = (before.get("budget") or {}).get("total")
    new_total = (after.get("budget") or {}).get("total")
    old_weather = {item.get("date"): item for item in before.get("weather_info", [])}
    new_weather = {item.get("date"): item for item in after.get("weather_info", [])}
    return {
        "changed_days": changes,
        "weather_changed_dates": [date for date in sorted(set(old_weather) | set(new_weather))
                                  if old_weather.get(date) != new_weather.get(date)],
        "budget_before": old_total,
        "budget_after": new_total,
        "budget_delta": new_total - old_total if isinstance(old_total, (int, float)) and isinstance(new_total, (int, float)) else None,
        "suggestions_changed": before.get("overall_suggestions") != after.get("overall_suggestions"),
    }
