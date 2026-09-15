"""行程修改锁定规则。

Revision Agent 可以理解“第一天和酒店不要改”这类自然语言，但最终是否真正保持不变
不交给 LLM 自己保证。该模块负责解析锁定意图，并在 Agent 修改后做确定性保护。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..models.schemas import RevisionLocks


_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_day_number(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        value = int(token)
        return value if value >= 1 else None
    if token in _CN_NUMBERS:
        return _CN_NUMBERS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in _CN_NUMBERS:
        return 10 + _CN_NUMBERS[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in _CN_NUMBERS:
        return _CN_NUMBERS[token[0]] * 10
    return None


def _normalise(locks: RevisionLocks) -> RevisionLocks:
    return RevisionLocks(
        locked_day_indexes=sorted({index for index in locks.locked_day_indexes if index >= 0}),
        lock_all_hotels=locks.lock_all_hotels,
        locked_attraction_names=sorted({name.strip() for name in locks.locked_attraction_names if name.strip()}),
    )


def merge_revision_locks(
    existing: RevisionLocks | Dict[str, Any] | None,
    feedback: str,
    *,
    explicit: RevisionLocks | None = None,
) -> RevisionLocks:
    """把会话已有锁、显式 API 锁与本次自然语言锁定意图合并。"""
    current = existing if isinstance(existing, RevisionLocks) else RevisionLocks(**(existing or {}))
    days = set(current.locked_day_indexes)
    attractions = set(current.locked_attraction_names)
    lock_hotels = current.lock_all_hotels

    if explicit is not None:
        days.update(explicit.locked_day_indexes)
        attractions.update(explicit.locked_attraction_names)
        lock_hotels = lock_hotels or explicit.lock_all_hotels

    text = feedback or ""
    lower = text.lower()
    unlock_mode = any(keyword in text for keyword in ("取消锁定", "解除锁定", "可以修改"))
    lock_mode = any(keyword in text for keyword in ("锁定", "不要改", "不能改", "不改", "保持不变", "已确定", "保留"))

    day_matches = re.findall(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*天", text)
    parsed_days = {
        value - 1
        for token in day_matches
        if (value := _parse_day_number(token)) is not None
    }
    if unlock_mode:
        days.difference_update(parsed_days)
    elif lock_mode:
        days.update(parsed_days)

    hotel_mentioned = "酒店" in text or "住宿" in text or "hotel" in lower
    if hotel_mentioned:
        if unlock_mode:
            lock_hotels = False
        elif lock_mode:
            lock_hotels = True

    # 支持“锁定景点：故宫、天坛”这种明确表达；复杂实体抽取仍留给后续 UI/API 显式传参。
    attraction_match = re.search(r"(?:锁定景点|保留景点)\s*[:：]\s*([^。；;\n]+)", text)
    if attraction_match and not unlock_mode:
        for name in re.split(r"[,，、]", attraction_match.group(1)):
            if name.strip():
                attractions.add(name.strip())

    unlock_attraction_match = re.search(r"(?:取消锁定景点|解除锁定景点)\s*[:：]\s*([^。；;\n]+)", text)
    if unlock_attraction_match:
        for name in re.split(r"[,，、]", unlock_attraction_match.group(1)):
            attractions.discard(name.strip())

    return _normalise(RevisionLocks(
        locked_day_indexes=list(days),
        lock_all_hotels=lock_hotels,
        locked_attraction_names=list(attractions),
    ))


def _find_day(plan: Dict[str, Any], day_index: int) -> Optional[Dict[str, Any]]:
    return next((day for day in plan.get("days", []) if int(day.get("day_index", -1)) == day_index), None)


def _find_attraction(plan: Dict[str, Any], name: str) -> Optional[Tuple[int, int, Dict[str, Any]]]:
    target = name.strip().casefold()
    for day_position, day in enumerate(plan.get("days", [])):
        for attraction_position, attraction in enumerate(day.get("attractions", [])):
            if str(attraction.get("name", "")).strip().casefold() == target:
                return day_position, attraction_position, attraction
    return None


def enforce_revision_locks(
    original_plan: Dict[str, Any],
    revised_plan: Dict[str, Any],
    locks: RevisionLocks,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """检测并恢复被 Agent 误改的锁定字段。

    返回保护后的 plan 和 violation 列表。violation 会进入 Execution Trace，
    因此不会静默掩盖 Agent 未遵守约束的事实。
    """
    protected = copy.deepcopy(revised_plan)
    violations: List[Dict[str, Any]] = []

    for day_index in locks.locked_day_indexes:
        original_day = _find_day(original_plan, day_index)
        revised_day = _find_day(protected, day_index)
        if original_day is None:
            continue
        if revised_day != original_day:
            violations.append({
                "type": "locked_day_changed",
                "day_index": day_index,
                "message": f"第 {day_index + 1} 天已锁定，Agent 修改结果被恢复。",
            })
            days = protected.setdefault("days", [])
            replaced = False
            for position, day in enumerate(days):
                if int(day.get("day_index", -1)) == day_index:
                    days[position] = copy.deepcopy(original_day)
                    replaced = True
                    break
            if not replaced:
                days.append(copy.deepcopy(original_day))
                days.sort(key=lambda item: int(item.get("day_index", 0)))

    if locks.lock_all_hotels:
        for original_day in original_plan.get("days", []):
            day_index = int(original_day.get("day_index", -1))
            revised_day = _find_day(protected, day_index)
            if revised_day is None:
                continue
            original_hotel = original_day.get("hotel")
            original_accommodation = original_day.get("accommodation")
            if revised_day.get("hotel") != original_hotel or revised_day.get("accommodation") != original_accommodation:
                violations.append({
                    "type": "locked_hotel_changed",
                    "day_index": day_index,
                    "message": f"第 {day_index + 1} 天酒店/住宿已锁定，Agent 修改结果被恢复。",
                })
                revised_day["hotel"] = copy.deepcopy(original_hotel)
                revised_day["accommodation"] = original_accommodation

    for name in locks.locked_attraction_names:
        original = _find_attraction(original_plan, name)
        if original is None:
            continue
        original_day_position, original_attraction_position, original_attraction = original
        revised = _find_attraction(protected, name)
        revised_matches = (
            revised is not None
            and revised[0] == original_day_position
            and revised[2] == original_attraction
        )
        if revised_matches:
            continue

        violations.append({
            "type": "locked_attraction_changed",
            "attraction": name,
            "message": f"景点“{name}”已锁定，Agent 修改结果被恢复到原日期与内容。",
        })

        # 移除修改结果中同名景点，防止重复，再恢复原对象和原位置。
        target = name.strip().casefold()
        for day in protected.get("days", []):
            day["attractions"] = [
                attraction
                for attraction in day.get("attractions", [])
                if str(attraction.get("name", "")).strip().casefold() != target
            ]

        if original_day_position < len(protected.get("days", [])):
            target_day = protected["days"][original_day_position]
            attractions = target_day.setdefault("attractions", [])
            insert_at = min(original_attraction_position, len(attractions))
            attractions.insert(insert_at, copy.deepcopy(original_attraction))

    return protected, violations
