"""Keep cost aggregates consistent after deterministic edits restore or remove items."""

from copy import deepcopy
from typing import Any


def _item_costs(plan: dict[str, Any]) -> dict[str, int]:
    days = plan.get("days", [])
    return {
        "total_attractions": sum(item.get("ticket_price", 0) or 0 for day in days for item in day.get("attractions", [])),
        "total_meals": sum(item.get("estimated_cost", 0) or 0 for day in days for item in day.get("meals", [])),
        "total_hotels": sum((day.get("hotel") or {}).get("estimated_cost", 0) or 0 for day in days),
    }


def adjust_budget_for_edits(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Apply item-cost deltas; retain existing night-count and transport estimates."""
    if not before.get("budget"):
        return after
    old_costs, new_costs = _item_costs(before), _item_costs(after)
    changed = any(old_costs[key] != new_costs[key] for key in old_costs)
    if not changed:
        return after
    result = deepcopy(after)
    budget = deepcopy(before["budget"])
    for key in old_costs:
        budget[key] = budget.get(key, 0) + new_costs[key] - old_costs[key]
    budget["total"] = sum(budget.get(key, 0) for key in (
        "total_attractions", "total_hotels", "total_meals", "total_transportation"))
    result["budget"] = budget
    return result
