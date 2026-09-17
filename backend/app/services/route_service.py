"""路线指标服务。

GIS 与 Validator 直接调用高德 Web 服务 REST API，避免每个路段都启动一个
``uvx amap-mcp-server`` 子进程。服务对地理编码与路线结果做内存缓存，且所有
外部请求都有确定超时。调用失败时由上层统一回退到 Haversine 坐标估算。

``parse_route_metrics`` 仍保留，用于兼容旧 Trace / MCP 输出的离线解析。
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, Iterable, Optional

import httpx

from ..config import get_settings, is_configured_secret


AMAP_REST_BASE_URL = "https://restapi.amap.com"


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

    def _try_parse(candidate: str) -> None:
        try:
            candidates.append(json.loads(candidate))
        except Exception:
            return

    _try_parse(text)

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL):
        _try_parse(match.group(1).strip())

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        _try_parse(text[first_brace:last_brace + 1])

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
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(raw)

    direct_candidates = [raw] if not isinstance(raw, str) else []
    for candidate in direct_candidates + _parse_json_candidates(text):
        parsed = _extract_from_json(candidate)
        if parsed["distance_meters"] is not None or parsed["duration_seconds"] is not None:
            return {**parsed, "raw": text}

    distance = None
    duration = None
    distance_pattern = r"(?:distance|距离)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*(km|公里|m|米)?"
    duration_pattern = r"(?:duration|耗时|时间)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*(小时|hour|hours|分钟|min|mins|秒|s)?"

    match = re.search(distance_pattern, text, re.IGNORECASE)
    if match:
        value = float(match.group(1))
        unit = (match.group(2) or "m").lower()
        distance = value * 1000 if unit in {"km", "公里"} else value

    match = re.search(duration_pattern, text, re.IGNORECASE)
    if match:
        value = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        if unit in {"小时", "hour", "hours"}:
            duration = value * 3600
        elif unit in {"分钟", "min", "mins"}:
            duration = value * 60
        else:
            duration = value

    return {"distance_meters": distance, "duration_seconds": duration, "raw": text}


def _coordinate(value: Any) -> Optional[str]:
    """把 Pydantic Location 或字典转换为高德 ``longitude,latitude`` 形式。"""
    if value is None:
        return None
    if isinstance(value, dict):
        longitude = value.get("longitude")
        latitude = value.get("latitude")
    else:
        longitude = getattr(value, "longitude", None)
        latitude = getattr(value, "latitude", None)
    if longitude is None or latitude is None:
        return None
    return f"{float(longitude):.6f},{float(latitude):.6f}"


class RouteService:
    """带超时、连接复用和路线缓存的高德 REST 路线服务。"""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        http_client: Optional[Any] = None,
    ):
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.amap_api_key
        if not is_configured_secret(self.api_key):
            raise ValueError("高德地图 Web 服务 Key 未配置")

        timeout = timeout_seconds or settings.amap_route_timeout_seconds
        self.http_client = http_client or httpx.Client(
            base_url=AMAP_REST_BASE_URL,
            timeout=httpx.Timeout(timeout),
        )
        self._geocode_cache: Dict[tuple[str, str], str] = {}
        self._route_cache: Dict[tuple[str, str, str, str, str], Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()

    def _get_json(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        response = self.http_client.get(path, params={**params, "key": self.api_key})
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status")) != "1":
            info = payload.get("info") or "unknown AMap error"
            infocode = payload.get("infocode") or "unknown"
            raise RuntimeError(f"AMap API failed: {info} ({infocode})")
        return payload

    def _geocode(self, address: str, city: Optional[str]) -> str:
        key = ((city or "").strip(), address.strip())
        with self._cache_lock:
            cached = self._geocode_cache.get(key)
        if cached:
            return cached

        payload = self._get_json(
            "/v3/geocode/geo",
            {"address": address, "city": city or "", "output": "JSON"},
        )
        geocodes = payload.get("geocodes") or []
        location = str(geocodes[0].get("location") or "") if geocodes else ""
        if not location:
            raise ValueError(f"高德无法解析地址: {address}")

        with self._cache_lock:
            self._geocode_cache[key] = location
        return location

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
    ) -> Dict[str, Any]:
        normalized_type = route_type if route_type in {"walking", "driving", "transit"} else "transit"
        origin = _coordinate(origin_location) or self._geocode(origin_address, origin_city)
        destination = _coordinate(destination_location) or self._geocode(destination_address, destination_city)
        cache_key = (
            origin,
            destination,
            normalized_type,
            (origin_city or "").strip(),
            (destination_city or "").strip(),
        )

        with self._cache_lock:
            cached = self._route_cache.get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        endpoint_map = {
            "walking": "/v3/direction/walking",
            "driving": "/v3/direction/driving",
            "transit": "/v3/direction/transit/integrated",
        }
        params: Dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "output": "JSON",
        }
        if normalized_type == "transit":
            params["city"] = origin_city or destination_city or ""
            params["cityd"] = destination_city or origin_city or ""

        payload = self._get_json(endpoint_map[normalized_type], params)
        route = payload.get("route") or {}
        candidates = route.get("transits") if normalized_type == "transit" else route.get("paths")
        first = candidates[0] if candidates else None
        if not isinstance(first, dict):
            raise ValueError("高德路线规划未返回可用方案")

        metrics = parse_route_metrics(first)
        if metrics["duration_seconds"] is None:
            raise ValueError("高德路线规划未返回耗时")

        result = {
            "distance_meters": metrics["distance_meters"],
            "duration_seconds": metrics["duration_seconds"],
            "route_type": normalized_type,
            "source": "amap_rest",
            "cached": False,
        }
        with self._cache_lock:
            self._route_cache[cache_key] = result
        return dict(result)

    def close(self) -> None:
        close = getattr(self.http_client, "close", None)
        if callable(close):
            close()


_route_service: Optional[RouteService] = None
_route_service_lock = threading.Lock()


def get_route_service() -> RouteService:
    global _route_service
    if _route_service is None:
        with _route_service_lock:
            if _route_service is None:
                _route_service = RouteService()
    return _route_service
