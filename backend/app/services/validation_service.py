"""旅行计划确定性校验。

Validator 不依赖另一个 LLM 来判断“好不好”，而是把可以明确检查的约束
（预算、每日强度、重复景点、相邻景点交通耗时等）做成可重复执行的规则。
路线优先使用高德 MCP；外部路线不可用时退化为经纬度距离估算，并显式记录来源。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models.schemas import Attraction, TripPlan, TripRequest
from .route_service import get_route_service


@dataclass
class ValidationIssue:
    code: str
    severity: str
    message: str
    day_index: Optional[int] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "day_index": self.day_index,
            "details": self.details,
        }


@dataclass
class ValidationReport:
    passed: bool
    issues: List[ValidationIssue]
    checked_route_segments: int = 0
    route_source_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def blocking_issues(self) -> List[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "blocking_issue_count": len(self.blocking_issues),
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_route_segments": self.checked_route_segments,
            "route_source_counts": self.route_source_counts,
        }


def _haversine_km(a: Attraction, b: Attraction) -> float:
    lat1 = math.radians(a.location.latitude)
    lon1 = math.radians(a.location.longitude)
    lat2 = math.radians(b.location.latitude)
    lon2 = math.radians(b.location.longitude)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(value))


def _route_type(transportation: str) -> str:
    text = transportation.lower()
    if "自驾" in transportation or "drive" in text:
        return "driving"
    if "步行" in transportation or "walk" in text:
        return "walking"
    return "transit"


def _estimate_minutes(distance_km: float, route_type: str) -> int:
    # 只作为 MCP 路线不可用时的 fallback；刻意使用保守速度。
    speed_kmh = {"walking": 4.5, "driving": 25.0, "transit": 18.0}.get(route_type, 18.0)
    return max(1, round(distance_km / speed_kmh * 60))


def validate_trip_plan(plan: TripPlan, request: TripRequest, *, check_routes: bool = True) -> ValidationReport:
    issues: List[ValidationIssue] = []
    checked_route_segments = 0
    route_source_counts: Dict[str, int] = {}
    constraints = request.constraints

    if len(plan.days) != request.travel_days:
        issues.append(ValidationIssue(
            code="day_count_mismatch",
            severity="error",
            message=f"计划包含 {len(plan.days)} 天，但请求要求 {request.travel_days} 天。",
        ))

    if constraints.max_budget is not None:
        if plan.budget is None:
            issues.append(ValidationIssue(
                code="budget_missing",
                severity="error",
                message="用户设置了预算上限，但计划没有生成预算。",
            ))
        elif plan.budget.total > constraints.max_budget:
            issues.append(ValidationIssue(
                code="budget_exceeded",
                severity="error",
                message=f"预计总预算 ¥{plan.budget.total} 超过上限 ¥{constraints.max_budget}。",
                details={"actual": plan.budget.total, "limit": constraints.max_budget},
            ))

    seen: Dict[str, int] = {}
    route_type = _route_type(request.transportation)
    route_service = None
    if check_routes:
        try:
            route_service = get_route_service()
        except Exception:
            route_service = None

    for day in plan.days:
        attractions = day.attractions
        if constraints.max_daily_attractions is not None and len(attractions) > constraints.max_daily_attractions:
            issues.append(ValidationIssue(
                code="too_many_attractions",
                severity="error",
                day_index=day.day_index,
                message=f"第 {day.day_index + 1} 天安排 {len(attractions)} 个景点，超过上限 {constraints.max_daily_attractions} 个。",
            ))

        visit_minutes = sum(max(0, item.visit_duration) for item in attractions)
        if visit_minutes > constraints.max_daily_visit_minutes:
            issues.append(ValidationIssue(
                code="daily_visit_time_exceeded",
                severity="error",
                day_index=day.day_index,
                message=f"第 {day.day_index + 1} 天景点游览时长约 {visit_minutes} 分钟，超过 {constraints.max_daily_visit_minutes} 分钟。",
                details={"actual_minutes": visit_minutes, "limit_minutes": constraints.max_daily_visit_minutes},
            ))

        for attraction in attractions:
            key = attraction.name.strip().lower()
            if key in seen:
                issues.append(ValidationIssue(
                    code="duplicate_attraction",
                    severity="error",
                    day_index=day.day_index,
                    message=f"景点“{attraction.name}”在多个日期重复安排。",
                    details={"first_day_index": seen[key], "duplicate_day_index": day.day_index},
                ))
            else:
                seen[key] = day.day_index

        for left, right in zip(attractions, attractions[1:]):
            checked_route_segments += 1
            straight_km = round(_haversine_km(left, right), 2)
            route_minutes: Optional[int] = None
            distance_km = straight_km
            source = "coordinate_estimate"

            if route_service is not None:
                try:
                    route = route_service.plan_route(
                        left.address or left.name,
                        right.address or right.name,
                        origin_city=request.city,
                        destination_city=request.city,
                        route_type=route_type,
                    )
                    if route.get("duration_seconds") is not None:
                        route_minutes = max(1, round(float(route["duration_seconds"]) / 60))
                        if route.get("distance_meters") is not None:
                            distance_km = round(float(route["distance_meters"]) / 1000, 2)
                        source = "amap_mcp"
                except Exception:
                    # 路线工具失败时仍可完成校验，但必须标记为估算来源。
                    route_minutes = None

            if route_minutes is None:
                route_minutes = _estimate_minutes(straight_km, route_type)

            route_source_counts[source] = route_source_counts.get(source, 0) + 1
            if route_minutes > constraints.max_route_minutes:
                issues.append(ValidationIssue(
                    code="route_leg_too_long",
                    severity="error",
                    day_index=day.day_index,
                    message=(
                        f"第 {day.day_index + 1} 天“{left.name} → {right.name}”预计交通约 "
                        f"{route_minutes} 分钟，超过单段上限 {constraints.max_route_minutes} 分钟。"
                    ),
                    details={
                        "from": left.name,
                        "to": right.name,
                        "route_minutes": route_minutes,
                        "distance_km": distance_km,
                        "source": source,
                    },
                ))
            elif straight_km > 20:
                issues.append(ValidationIssue(
                    code="long_distance_leg",
                    severity="warning",
                    day_index=day.day_index,
                    message=f"第 {day.day_index + 1} 天存在约 {straight_km} km 的跨区移动，请确认行程强度。",
                    details={"from": left.name, "to": right.name, "source": source},
                ))

    blocking = [issue for issue in issues if issue.severity == "error"]
    return ValidationReport(
        passed=not blocking,
        issues=issues,
        checked_route_segments=checked_route_segments,
        route_source_counts=route_source_counts,
    )
