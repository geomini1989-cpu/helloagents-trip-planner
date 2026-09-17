"""GIS 路线优化服务。

把 LLM 生成的“景点集合”转换为一个确定性的空间优化问题：
1. 使用经纬度计算 Haversine 空间距离；
2. 小规模日行程优先使用高德 MCP 构建真实路网时间矩阵；
3. 对候选顺序做精确枚举，选择总交通成本更低的路线；
4. 外部路网不可用时退化为坐标距离估算，并显式记录数据来源。

LLM 负责“去哪里”，GIS/路网负责“按什么顺序去”。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from ..models.schemas import Attraction, Location, TripPlan, TripRequest
from .route_service import get_route_service


NETWORK_MATRIX_LIMIT = 5
MIN_SAVING_TO_REORDER_MINUTES = 3


class RouteProvider(Protocol):
    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        *,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "transit",
        origin_location: Any = None,
        destination_location: Any = None,
    ) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class GeoPoint:
    key: str
    name: str
    address: str
    location: Location


@dataclass
class RouteCost:
    duration_minutes: int
    distance_km: float
    source: str


@dataclass
class DayRouteOptimization:
    day_index: int
    original_order: List[str]
    optimized_order: List[str]
    before_minutes: int
    after_minutes: int
    saved_minutes: int
    reordered: bool
    route_type: str
    source_counts: Dict[str, int] = field(default_factory=dict)
    evaluated_permutations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "day_index": self.day_index,
            "original_order": self.original_order,
            "optimized_order": self.optimized_order,
            "before_minutes": self.before_minutes,
            "after_minutes": self.after_minutes,
            "saved_minutes": self.saved_minutes,
            "reordered": self.reordered,
            "route_type": self.route_type,
            "source_counts": self.source_counts,
            "evaluated_permutations": self.evaluated_permutations,
        }


@dataclass
class RouteOptimizationReport:
    days: List[DayRouteOptimization]

    @property
    def reordered_days(self) -> int:
        return sum(day.reordered for day in self.days)

    @property
    def saved_minutes(self) -> int:
        return sum(day.saved_minutes for day in self.days)

    def to_dict(self) -> Dict[str, Any]:
        source_counts: Dict[str, int] = {}
        for day in self.days:
            for key, value in day.source_counts.items():
                source_counts[key] = source_counts.get(key, 0) + value
        return {
            "optimized_days": len(self.days),
            "reordered_days": self.reordered_days,
            "saved_minutes": self.saved_minutes,
            "source_counts": source_counts,
            "days": [day.to_dict() for day in self.days],
        }


def _route_type(transportation: str) -> str:
    text = transportation.lower()
    if "自驾" in transportation or "drive" in text:
        return "driving"
    if any(keyword in transportation for keyword in ("公共交通", "公交", "地铁")) or "transit" in text:
        return "transit"
    if "步行" in transportation or "walk" in text:
        return "walking"
    return "transit"


def _haversine_km(a: Location, b: Location) -> float:
    lat1 = math.radians(a.latitude)
    lon1 = math.radians(a.longitude)
    lat2 = math.radians(b.latitude)
    lon2 = math.radians(b.longitude)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(value))


def _estimate_minutes(distance_km: float, route_type: str) -> int:
    speed_kmh = {"walking": 4.5, "driving": 25.0, "transit": 18.0}.get(route_type, 18.0)
    return max(1, round(distance_km / speed_kmh * 60))


def _attraction_point(index: int, attraction: Attraction) -> GeoPoint:
    return GeoPoint(
        key=f"attraction:{index}",
        name=attraction.name,
        address=attraction.address or attraction.name,
        location=attraction.location,
    )


def _fallback_cost(origin: GeoPoint, destination: GeoPoint, route_type: str) -> RouteCost:
    distance_km = round(_haversine_km(origin.location, destination.location), 2)
    return RouteCost(
        duration_minutes=_estimate_minutes(distance_km, route_type),
        distance_km=distance_km,
        source="haversine_estimate",
    )


def _route_cost(
    origin: GeoPoint,
    destination: GeoPoint,
    *,
    city: str,
    route_type: str,
    route_provider: Optional[RouteProvider],
    allow_network: bool,
) -> RouteCost:
    fallback = _fallback_cost(origin, destination, route_type)
    if not allow_network or route_provider is None:
        return fallback

    try:
        route = route_provider.plan_route(
            origin.address,
            destination.address,
            origin_city=city,
            destination_city=city,
            route_type=route_type,
            origin_location=origin.location,
            destination_location=destination.location,
        )
        duration = route.get("duration_seconds")
        if duration is None:
            return fallback
        distance = route.get("distance_meters")
        return RouteCost(
            duration_minutes=max(1, round(float(duration) / 60)),
            distance_km=(round(float(distance) / 1000, 2) if distance is not None else fallback.distance_km),
            source="amap_network",
        )
    except Exception:
        return fallback


def _path_cost(order: Tuple[int, ...], matrix: Dict[Tuple[int, int], RouteCost]) -> int:
    return sum(matrix[(left, right)].duration_minutes for left, right in zip(order, order[1:]))


def optimize_day_attractions(
    attractions: List[Attraction],
    *,
    day_index: int,
    city: str,
    transportation: str,
    route_provider: Optional[RouteProvider] = None,
) -> tuple[List[Attraction], DayRouteOptimization]:
    """优化一天内景点访问顺序。

    旅行计划通常每天 2-3 个景点，因此使用精确枚举而不是引入复杂启发式算法。
    当景点数超过 5 时，为避免大量 MCP 路网调用，矩阵切换为纯 GIS 坐标估算；
    Validator 仍会对最终相邻路段再调用真实路线工具校验。
    """
    original_names = [item.name for item in attractions]
    route_type = _route_type(transportation)
    count = len(attractions)
    if count < 2:
        report = DayRouteOptimization(
            day_index=day_index,
            original_order=original_names,
            optimized_order=original_names,
            before_minutes=0,
            after_minutes=0,
            saved_minutes=0,
            reordered=False,
            route_type=route_type,
            evaluated_permutations=1,
        )
        return list(attractions), report

    points = [_attraction_point(index, attraction) for index, attraction in enumerate(attractions)]
    allow_network = count <= NETWORK_MATRIX_LIMIT
    if route_provider is None and allow_network:
        try:
            route_provider = get_route_service()
        except Exception:
            route_provider = None

    matrix: Dict[Tuple[int, int], RouteCost] = {}
    source_counts: Dict[str, int] = {}
    for left in range(count):
        for right in range(count):
            if left == right:
                continue
            cost = _route_cost(
                points[left],
                points[right],
                city=city,
                route_type=route_type,
                route_provider=route_provider,
                allow_network=allow_network,
            )
            matrix[(left, right)] = cost
            source_counts[cost.source] = source_counts.get(cost.source, 0) + 1

    original_order = tuple(range(count))
    before_minutes = _path_cost(original_order, matrix)

    best_order = original_order
    best_minutes = before_minutes
    evaluated = 0
    for order in itertools.permutations(range(count)):
        evaluated += 1
        minutes = _path_cost(order, matrix)
        if minutes < best_minutes:
            best_order = order
            best_minutes = minutes

    saving = max(0, before_minutes - best_minutes)
    should_reorder = best_order != original_order and saving >= MIN_SAVING_TO_REORDER_MINUTES
    final_order = best_order if should_reorder else original_order
    final_minutes = best_minutes if should_reorder else before_minutes
    optimized = [attractions[index] for index in final_order]

    report = DayRouteOptimization(
        day_index=day_index,
        original_order=original_names,
        optimized_order=[item.name for item in optimized],
        before_minutes=before_minutes,
        after_minutes=final_minutes,
        saved_minutes=(saving if should_reorder else 0),
        reordered=should_reorder,
        route_type=route_type,
        source_counts=source_counts,
        evaluated_permutations=evaluated,
    )
    return optimized, report


def optimize_trip_routes(
    plan: TripPlan,
    request: TripRequest,
    *,
    route_provider: Optional[RouteProvider] = None,
) -> tuple[TripPlan, RouteOptimizationReport]:
    """对 TripPlan 每一天的景点顺序做 GIS / 路网优化，返回新的 TripPlan。"""
    payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    optimized_plan = TripPlan(**payload)
    reports: List[DayRouteOptimization] = []

    for day in optimized_plan.days:
        optimized, report = optimize_day_attractions(
            day.attractions,
            day_index=day.day_index,
            city=request.city,
            transportation=day.transportation or request.transportation,
            route_provider=route_provider,
        )
        day.attractions = optimized
        reports.append(report)

    return optimized_plan, RouteOptimizationReport(days=reports)
