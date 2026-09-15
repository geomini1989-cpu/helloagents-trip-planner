"""把常见自然语言旅行要求收敛为结构化硬约束。"""

from __future__ import annotations

import re

from ..models.schemas import TripRequest


def _first_int(patterns: list[str], text: str) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def merge_constraints_from_text(request: TripRequest) -> TripRequest:
    """从 free_text_input 中提取可明确量化的限制。

    显式 API constraints 仍然有效；自然语言只覆盖能够明确识别出的字段。
    这一步不尝试理解主观偏好，避免把模糊表达错误地变成硬约束。
    """
    text = (request.free_text_input or "").strip()
    if not text:
        return request

    constraints = request.constraints.model_copy(deep=True)

    budget = _first_int([
        r"(?:总?预算|花费|费用)[^\d]{0,12}(?:不超过|控制在|最多|以内)?[^\d]{0,6}(\d{2,7})\s*元",
        r"(?:不超过|控制在|最多)[^\d]{0,6}(\d{2,7})\s*元(?:以内)?",
    ], text)
    if budget is not None:
        constraints.max_budget = budget

    max_attractions = _first_int([
        r"每天[^\d]{0,10}(?:最多|不超过)[^\d]{0,4}(\d+)\s*个?(?:景点|地方)",
        r"每天[^\d]{0,6}(\d+)\s*个?(?:景点|地方)(?:以内)?",
    ], text)
    if max_attractions is not None and 1 <= max_attractions <= 8:
        constraints.max_daily_attractions = max_attractions

    max_route = _first_int([
        r"(?:单段|单程|景点之间|交通)[^\d]{0,12}(?:最多|不超过|控制在)?[^\d]{0,4}(\d+)\s*分钟",
    ], text)
    if max_route is not None and 10 <= max_route <= 240:
        constraints.max_route_minutes = max_route

    visit_hours = _first_int([
        r"每天[^\d]{0,12}(?:游玩|游览|逛)[^\d]{0,8}(?:最多|不超过)?[^\d]{0,4}(\d+)\s*小时",
    ], text)
    if visit_hours is not None and 2 <= visit_hours <= 15:
        constraints.max_daily_visit_minutes = visit_hours * 60

    visit_minutes = _first_int([
        r"每天[^\d]{0,12}(?:游玩|游览|逛)[^\d]{0,8}(?:最多|不超过)?[^\d]{0,4}(\d+)\s*分钟",
    ], text)
    if visit_minutes is not None and 120 <= visit_minutes <= 900:
        constraints.max_daily_visit_minutes = visit_minutes

    return request.model_copy(update={"constraints": constraints})
