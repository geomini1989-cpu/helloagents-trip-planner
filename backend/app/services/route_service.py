"""路线指标服务。

把高德 MCP 的路线工具结果收敛为稳定的 distance / duration 字段，供 Validator 使用。
MCP 输出格式可能因版本而不同，因此解析器同时支持 JSON 与文本形式。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Optional

from .amap_service import get_amap_mcp_tool


def _iter_nodes(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nodes(child)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return float(match.group())
    return None


def _parse_json_candidates(raw: str) -> list[Any]:
    candidates: list[Any] = []
    text = raw.strip()
    try:
        candidates.append(json.loads(text))
    except Exception:
        pass

    for match in re.finditer(r"\{.*?\}", text, re.DOTALL):
        try:
            candidates.append(json.loads(match.group()))
        except Exception:
            continue
    return candidates


def _extract_from_json(value: Any) -> Dict[str, Optional[float]]:
    distance_keys = {"distance", "distance_meters", "distance_meter", "length"}
    duration_keys = {"duration", "duration_seconds", "time", "cost_time"}

    distance = None
    duration = None
    for node in _iter_nodes(value):
        if not isinstance(node, dict):
            continue
        for key, item in node.items():
            normalized = str(key).lower()
            if distance is None and normalized in distance_keys:
                distance = _number(item)
            if duration is None and normalized in duration_keys:
                duration = _number(item)
        if distance is not None and duration is not None:
            break
    return {"distance_meters": distance, "duration_seconds": duration}


def parse_route_metrics(raw: Any) -> Dict[str, Any]:
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)

    for candidate in ([raw] if not isinstance(raw, str) else []) + _parse_json_candidates(text):
        parsed = _extract_from_json(candidate)
        if parsed["distance_meters"] is not None or parsed["duration_seconds"] is not None:
            return {**parsed, "raw": text}

    distance = None
    duration = None

    distance_patterns = [
        r"(?:distance|距离)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*(km|公里|m|米)?",
    ]
    duration_patterns = [
        r"(?:duration|耗时|时间)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*(小时|hour|hours|分钟|min|mins|秒|s)?",
    ]

    for pattern in distance_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = (match.group(2) or "m").lower()
            distance = value * 1000 if unit in {"km", "公里"} else value
            break

    for pattern in duration_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = (match.group(2) or "s").lower()
            if unit in {"小时", "hour", "hours"}:
                duration = value * 3600
            elif unit in {"分钟", "min", "mins"}:
                duration = value * 60
            else:
                duration = value
            break

    return {"distance_meters": distance, "duration_seconds": duration, "raw": text}


class RouteService:
    def __init__(self):
        self.mcp_tool = get_amap_mcp_tool()

    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        *,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "transit",
    ) -> Dict[str, Any]:
        tool_map = {
            "walking": "maps_direction_walking_by_address",
            "driving": "maps_direction_driving_by_address",
            "transit": "maps_direction_transit_integrated_by_address",
        }
        tool_name = tool_map.get(route_type, tool_map["transit"])
        arguments: Dict[str, Any] = {
            "origin_address": origin_address,
            "destination_address": destination_address,
        }
        if origin_city:
            arguments["origin_city"] = origin_city
        if destination_city:
            arguments["destination_city"] = destination_city

        raw = self.mcp_tool.run({
            "action": "call_tool",
            "tool_name": tool_name,
            "arguments": arguments,
        })
        result = parse_route_metrics(raw)
        result["tool_name"] = tool_name
        return result


_route_service: Optional[RouteService] = None


def get_route_service() -> RouteService:
    global _route_service
    if _route_service is None:
        _route_service = RouteService()
    return _route_service
