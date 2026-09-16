"""Coordinator / Router for dynamic multi-agent orchestration.

The LLM may classify intent and suggest capabilities, but it never directly controls
which Python functions or tools execute. The backend converts the intent into a
canonical, allow-listed execution graph with deterministic dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

from hello_agents import SimpleAgent

from ..services.llm_service import get_llm


COORDINATOR_PROMPT = """你是旅行 Agent 系统的 Coordinator。你的任务只是在现有能力目录中判断用户请求属于哪种任务。

允许的 intent 只有：
- full_plan: 从零生成完整旅行计划
- general_revision: 对已有计划做一般局部修改
- weather_replan: 因天气/降雨/温度变化调整已有计划
- hotel_change: 只调整酒店/住宿相关内容
- route_optimize: 只优化已有景点的访问顺序或交通路线

允许的 capability 只有：
attraction, weather, hotel, planner, revision, gis, validator, repair

只输出 JSON：
{
  "intent": "weather_replan",
  "requested_capabilities": ["weather", "revision", "gis", "validator"],
  "reason": "用户要求根据降雨调整已有行程"
}

不要输出工具参数，不要输出 Python 函数名，不要创建新的 capability。
"""


@dataclass(frozen=True)
class ExecutionNode:
    id: str
    capability: str
    depends_on: List[str]
    conditional: bool = False


@dataclass(frozen=True)
class ExecutionPlan:
    intent: str
    nodes: List[ExecutionNode]
    source: str
    reason: str = ""

    @property
    def capabilities(self) -> List[str]:
        return [node.capability for node in self.nodes]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "source": self.source,
            "reason": self.reason,
            "capabilities": self.capabilities,
            "nodes": [asdict(node) for node in self.nodes],
        }


# Canonical graphs are backend-owned. LLM output can select an intent, not invent execution edges.
_CANONICAL_GRAPHS: Dict[str, List[ExecutionNode]] = {
    "full_plan": [
        ExecutionNode("attraction", "attraction", []),
        ExecutionNode("weather", "weather", []),
        ExecutionNode("hotel", "hotel", []),
        ExecutionNode("planner", "planner", ["attraction", "weather", "hotel"]),
        ExecutionNode("gis", "gis", ["planner"]),
        ExecutionNode("validator", "validator", ["gis"]),
        ExecutionNode("repair", "repair", ["validator"], conditional=True),
    ],
    "general_revision": [
        ExecutionNode("revision", "revision", []),
        ExecutionNode("gis", "gis", ["revision"]),
        ExecutionNode("validator", "validator", ["gis"]),
    ],
    "weather_replan": [
        ExecutionNode("weather", "weather", []),
        ExecutionNode("revision", "revision", ["weather"]),
        ExecutionNode("gis", "gis", ["revision"]),
        ExecutionNode("validator", "validator", ["gis"]),
    ],
    "hotel_change": [
        ExecutionNode("hotel", "hotel", []),
        ExecutionNode("revision", "revision", ["hotel"]),
        ExecutionNode("gis", "gis", ["revision"]),
        ExecutionNode("validator", "validator", ["gis"]),
    ],
    "route_optimize": [
        ExecutionNode("gis", "gis", []),
        ExecutionNode("validator", "validator", ["gis"]),
    ],
}

_ALLOWED_INTENTS = frozenset(_CANONICAL_GRAPHS)


def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        raise ValueError("empty coordinator response")
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        text = text[start:end]
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end]
    elif "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text.strip())


def _heuristic_intent(message: str, *, has_session: bool) -> str:
    text = (message or "").lower()
    if not has_session:
        return "full_plan"

    weather_terms = ("天气", "下雨", "降雨", "雨天", "晴天", "温度", "weather", "rain")
    hotel_terms = ("酒店", "住宿", "宾馆", "hotel", "accommodation")
    route_terms = ("路线", "顺序", "怎么排", "交通", "最省时间", "最短", "route", "order")

    if any(term in text for term in weather_terms):
        return "weather_replan"
    if any(term in text for term in hotel_terms):
        return "hotel_change"
    if any(term in text for term in route_terms) and not re.search(r"增加|删除|换|改景点|新增", text):
        return "route_optimize"
    return "general_revision"


def _canonical_plan(intent: str, *, source: str, reason: str = "") -> ExecutionPlan:
    safe_intent = intent if intent in _ALLOWED_INTENTS else "general_revision"
    return ExecutionPlan(
        intent=safe_intent,
        nodes=list(_CANONICAL_GRAPHS[safe_intent]),
        source=source,
        reason=reason,
    )


def validate_coordinator_output(
    payload: Dict[str, Any],
    *,
    has_session: bool,
) -> ExecutionPlan:
    """Convert untrusted Coordinator output into a backend-owned execution graph."""
    intent = str(payload.get("intent", "")).strip()
    reason = str(payload.get("reason", "")).strip()

    if not has_session:
        # A request without a persisted plan cannot execute revision-only graphs.
        intent = "full_plan"
    elif intent == "full_plan":
        # In a continuation session, a generic re-plan request is handled as revision
        # so confirmed state and locks are retained.
        intent = "general_revision"

    if intent not in _ALLOWED_INTENTS:
        intent = "general_revision" if has_session else "full_plan"

    # requested_capabilities is deliberately not trusted for graph construction.
    return _canonical_plan(intent, source="llm", reason=reason)


def build_execution_plan(
    message: str,
    *,
    has_session: bool,
    llm_runner: Optional[Callable[[str], str]] = None,
) -> ExecutionPlan:
    """Classify a user request and return an allow-listed execution plan.

    Production uses a fresh request-scoped Coordinator Agent. Unit tests can inject
    llm_runner. If the LLM fails or returns invalid JSON, deterministic routing keeps
    the system available and makes the fallback visible through plan.source.
    """
    prompt = f"{COORDINATOR_PROMPT}\n\n用户请求：\n{message}"

    try:
        if llm_runner is None:
            agent = SimpleAgent(
                name="旅行任务 Coordinator",
                llm=get_llm(),
                system_prompt=COORDINATOR_PROMPT,
            )
            response = agent.run(f"用户请求：{message}")
        else:
            response = llm_runner(prompt)
        return validate_coordinator_output(_extract_json(response), has_session=has_session)
    except Exception:
        intent = _heuristic_intent(message, has_session=has_session)
        return _canonical_plan(
            intent,
            source="heuristic_fallback",
            reason="Coordinator LLM 不可用或输出无效，使用确定性意图路由。",
        )
