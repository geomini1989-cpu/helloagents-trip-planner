"""Keep explicitly scoped revisions within the days named by the user."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Set


_DAY_RE = re.compile(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*天")
_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CHANGE = re.compile(r"改|换|调整|安排|加入|加上|增加|删除|去|优化|重排|重新|取消|替换")
_LOCK = re.compile(r"锁定|不要改|不能改|不改|保留|保持不变|已确定")


def _day_number(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    if token in _CN:
        return _CN[token]
    if token.startswith("十") and token[1:] in _CN:
        return 10 + _CN[token[1:]]
    if token.endswith("十") and token[:-1] in _CN:
        return _CN[token[:-1]] * 10
    return None


def targeted_day_indexes(feedback: str, plan: Dict[str, Any]) -> Set[int]:
    """Return an explicit change target, or empty for ambiguous/global feedback.

    A mention such as '第一天不要改，第二天换景点' targets only day two.
    We deliberately do not infer dates from words like 'tomorrow'.
    """
    days = {int(day["day_index"]) for day in plan.get("days", [])}
    targets: Set[int] = set()
    clauses = re.split(r"[，,；;。\n]", feedback)
    for clause in clauses:
        matches = list(_DAY_RE.finditer(clause))
        for position, match in enumerate(matches):
            number = _day_number(match.group(1))
            if number is None or number - 1 not in days:
                continue
            start = match.end()
            end = matches[position + 1].start() if position + 1 < len(matches) else len(clause)
            segment = clause[start:end]
            prefix = clause[max(0, match.start() - 8):match.start()]
            if _LOCK.search(segment) or _LOCK.search(prefix):
                continue
            if _CHANGE.search(segment) or _CHANGE.search(prefix) or (len(matches) > 1 and _CHANGE.search(clause)):
                targets.add(number - 1)
        for day in plan.get("days", []):
            date = str(day.get("date") or "")
            if date and date in clause and _CHANGE.search(clause) and not _LOCK.search(clause):
                targets.add(int(day["day_index"]))
    return targets


def enforce_revision_scope(
    original_plan: Dict[str, Any], revised_plan: Dict[str, Any], targets: Set[int]
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if not targets:
        return revised_plan, []
    protected = copy.deepcopy(revised_plan)
    violations: List[Dict[str, Any]] = []
    original_days = original_plan.get("days", [])
    revised_by_index = {int(day.get("day_index", -1)): day for day in protected.get("days", [])}
    merged_days = []
    for original_day in original_days:
        index = int(original_day["day_index"])
        revised_day = revised_by_index.get(index)
        if index not in targets:
            if revised_day != original_day:
                violations.append({"type": "out_of_scope_day_changed", "day_index": index,
                                   "message": f"第 {index + 1} 天不在本次修改范围，已恢复。"})
            merged_days.append(copy.deepcopy(original_day))
        else:
            revised_target = copy.deepcopy(revised_day or original_day)
            revised_target["day_index"] = index
            revised_target["date"] = original_day.get("date")
            merged_days.append(revised_target)
    protected["days"] = merged_days
    target_dates = {str(day.get("date")) for day in original_days if int(day["day_index"]) in targets}
    old_weather = {str(item.get("date")): item for item in original_plan.get("weather_info", [])}
    new_weather = {str(item.get("date")): item for item in protected.get("weather_info", [])}
    if old_weather:
        protected["weather_info"] = [
            copy.deepcopy(new_weather.get(day, old_weather.get(day)))
            if day in target_dates else copy.deepcopy(old_weather[day])
            for day in old_weather
        ]
    protected["city"] = original_plan.get("city")
    protected["start_date"] = original_plan.get("start_date")
    protected["end_date"] = original_plan.get("end_date")
    return protected, violations
