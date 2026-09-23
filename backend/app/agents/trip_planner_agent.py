"""多智能体旅行规划系统。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from hello_agents import SimpleAgent
from hello_agents.tools import MCPTool

from ..config import get_settings
from ..models.schemas import DayPlan, RevisionLocks, TripPlan, TripRequest
from ..services.llm_service import get_llm
from ..services.local_knowledge_service import (
    LocalKnowledgeResult,
    normalize_agent_claims,
    search_local_knowledge,
)
from ..services.resilience_service import run_with_retry


ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。你的任务是根据城市和用户偏好搜索合适的景点。

你必须使用 amap MCP 网关调用真实工具，不要自己编造景点信息。
调用 maps_text_search 时严格输出 JSON 参数：
[TOOL_CALL:amap:{"action":"call_tool","tool_name":"maps_text_search","arguments":{"keywords":"景点关键词","city":"城市名"}}]

如果工具返回错误、工具不存在或调用失败，最终回答必须以 TOOL_ERROR: 开头，不要伪造景点数据。
"""

WEATHER_AGENT_PROMPT = """你是天气查询专家。你的任务是查询指定城市的天气信息。

你必须使用 amap MCP 网关调用真实工具，不要自己编造天气信息。
调用 maps_weather 时严格输出 JSON 参数：
[TOOL_CALL:amap:{"action":"call_tool","tool_name":"maps_weather","arguments":{"city":"城市名"}}]

如果工具返回错误、工具不存在或调用失败，最终回答必须以 TOOL_ERROR: 开头，不要伪造天气数据。
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。你的任务是根据城市和住宿偏好搜索合适的酒店。

你必须使用 amap MCP 网关调用真实工具，不要自己编造酒店信息。
调用 maps_text_search 时严格输出 JSON 参数：
[TOOL_CALL:amap:{"action":"call_tool","tool_name":"maps_text_search","arguments":{"keywords":"酒店","city":"城市名"}}]

如果工具返回错误、工具不存在或调用失败，最终回答必须以 TOOL_ERROR: 开头，不要伪造酒店数据。
"""

LOCAL_KNOWLEDGE_AGENT_PROMPT = """你是旅行景点本地知识核验专家。

你的职责不是推荐更多景点，而是只基于 Web Search 提供的候选来源，核验候选景点的运营规则：
- 开放时间 / 停止入场时间
- 是否需要预约、实名或提前购票
- 固定闭馆日
- 门票或入场限制
- 临时关闭、节假日特殊公告

规则：
1. 只能引用本轮输入中实际提供的 URL，禁止生成新 URL。
2. 有明确来源支撑的事实使用 verification_status=verified，并填写对应 source_url。
3. 信息不足或来源表述不明确时使用 verification_status=unverified，source_url 可以为空。
4. 不允许根据常识补造开放时间、门票、预约或临时公告。
5. 只输出完整合法 JSON，不要解释，不要 Markdown。

输出结构：
{
  "claims": [
    {
      "attraction": "故宫",
      "claim_type": "reservation",
      "claim": "参观需要提前预约",
      "verification_status": "verified",
      "source_url": "https://本轮候选来源中的真实URL"
    }
  ],
  "notes": []
}

claim_type 只使用：opening_hours, reservation, closure, ticket, admission, temporary_notice, other。
"""

PLANNER_AGENT_PROMPT = """你是行程规划专家。根据真实景点、天气、酒店、本地运营规则和结构化硬约束，生成可执行的旅行计划。

只输出完整、合法的 JSON。结构必须符合：
{
  "city": "城市",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "当日概述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {"longitude": 116.397128, "latitude": 39.916527},
        "price_range": "300-500元",
        "rating": "4.5",
        "distance": "距离主要景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 120,
          "description": "景点描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [
    {
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "多云",
      "day_temp": 25,
      "night_temp": 15,
      "wind_direction": "南风",
      "wind_power": "1-3级"
    }
  ],
  "overall_suggestions": "总体建议",
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 480,
    "total_transportation": 200,
    "total": 2060
  }
}

规则：
1. weather_info 尽量覆盖每一天；温度使用纯数字。
2. 每天必须包含早餐、午餐、晚餐。
3. 景点名称、地址、经纬度优先使用检索结果，不要凭空创造不存在的地点。
4. 必须给出预算，并确保 budget.total 等于四个分项之和。
5. day_index 从 0 连续递增。
6. 硬约束优先级高于“尽量多安排景点”，必须遵守预算、每日景点数、游览总时长和交通时长限制。
7. 尽量把同一区域的景点安排在同一天，减少跨区折返；最终顺序还会由 GIS 路线优化层处理。
8. Local Knowledge 输入中的 verified_claims 才能作为运营事实；unverified_claims 只能作为提醒。
"""

REVISE_AGENT_PROMPT = """你是行程规划修正专家。根据用户修改意见对当前 JSON 行程做局部调整。

要求：
1. 只修改用户提到的部分，其他内容尽量保持原样。
2. 输出完整合法 JSON，不要解释。
3. 不要创造不存在的 POI。
4. revision_locks 中列出的日期、酒店或景点属于硬锁定内容，绝对不能修改、删除或移动。

Revision locks：
{locks_json}

当前行程：
{current_plan_json}

用户修改意见：
{user_feedback}
"""

VALIDATION_REPAIR_PROMPT = """你是旅行计划修正 Agent。下方行程已经经过确定性 Validator 检查并发现硬冲突。

你的任务不是重新自由规划，而是以最小改动修复列出的 validation issues，同时保持没有问题的安排不变。
必须严格遵守结构化约束，只输出完整合法 JSON，不要解释。

结构化约束：
{constraints_json}

Validation issues：
{issues_json}

当前 TripPlan：
{current_plan_json}
"""


class MultiAgentTripPlanner:
    """多智能体旅行规划系统。

    LLM 与 MCP 配置可复用，但 SimpleAgent 不跨请求复用，避免内部 history
    在不同用户、不同 Eval case 之间产生上下文污染。
    """

    def __init__(self):
        settings = get_settings()
        self.llm = get_llm()
        # 固定使用单一 MCP 网关工具。Windows / 不同 MCP Server 版本下，
        # 子工具自动发现可能为空；由 Agent 通过 action=call_tool 显式选择 maps_* 子工具。
        self.amap_tool = MCPTool(
            name="amap",
            description="高德地图 MCP 网关，支持通过 call_tool 调用 POI、天气、酒店和路线子工具",
            server_command=["uvx", "amap-mcp-server"],
            env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
            auto_expand=False,
        )

    def _create_tool_agent(self, *, name: str, prompt: str) -> SimpleAgent:
        agent = SimpleAgent(name=name, llm=self.llm, system_prompt=prompt)
        agent.add_tool(self.amap_tool)
        return agent

    def create_planner_agent(self) -> SimpleAgent:
        return SimpleAgent(name="行程规划专家", llm=self.llm, system_prompt=PLANNER_AGENT_PROMPT)

    def create_local_knowledge_agent(self) -> SimpleAgent:
        return SimpleAgent(name="本地知识核验专家", llm=self.llm, system_prompt=LOCAL_KNOWLEDGE_AGENT_PROMPT)

    def create_request_agents(self) -> Dict[str, SimpleAgent]:
        return {
            "attraction": self._create_tool_agent(name="景点搜索专家", prompt=ATTRACTION_AGENT_PROMPT),
            "weather": self._create_tool_agent(name="天气查询专家", prompt=WEATHER_AGENT_PROMPT),
            "hotel": self._create_tool_agent(name="酒店推荐专家", prompt=HOTEL_AGENT_PROMPT),
            "local_knowledge": self.create_local_knowledge_agent(),
            "planner": self.create_planner_agent(),
        }

    def research_local_knowledge(
        self,
        request: TripRequest,
        attraction_context: str,
    ) -> tuple[str, LocalKnowledgeResult]:
        """Search independent sources, then deterministically verify Agent citations."""
        result = search_local_knowledge(
            city=request.city,
            attraction_context=attraction_context,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        source_context = result.as_agent_context()
        if result.degraded or not result.sources:
            return source_context, result

        agent = self.create_local_knowledge_agent()
        prompt = f"""旅行城市：{request.city}
旅行日期：{request.start_date} 至 {request.end_date}

候选景点检索上下文：
{attraction_context[:5000]}

Web Search 候选来源：
{source_context}

请只基于这些来源核验对行程真正有影响的开放、预约、闭馆、票务和临时公告信息，并严格按系统指定 JSON schema 输出。
"""
        response = agent.run(prompt)
        planner_context = normalize_agent_claims(response, result)
        return planner_context, result

    def plan_trip(self, request: TripRequest) -> TripPlan:
        """兼容旧调用方式；API 主流程使用 Orchestrator。"""
        try:
            agents = self.create_request_agents()
            attraction_response = agents["attraction"].run(self._build_attraction_query(request))
            weather_response = agents["weather"].run(f"请查询{request.city}的天气信息")
            hotel_response = agents["hotel"].run(f"请搜索{request.city}的{request.accommodation}酒店")
            local_knowledge, _ = self.research_local_knowledge(request, attraction_response)
            planner_response = agents["planner"].run(
                self._build_planner_query(
                    request,
                    attraction_response,
                    weather_response,
                    hotel_response,
                    local_knowledge,
                )
            )
            return self._parse_response(planner_response, request)
        except Exception as exc:
            print(f"❌ 生成旅行计划失败: {exc}")
            return self._create_fallback_plan(request)

    def revise_trip(
        self,
        current_plan: Dict[str, Any],
        user_feedback: str,
        locks: RevisionLocks | None = None,
    ) -> Dict[str, Any]:
        settings = get_settings()
        effective_locks = locks or RevisionLocks()
        locks_payload = effective_locks.model_dump() if hasattr(effective_locks, "model_dump") else effective_locks.dict()
        prompt = REVISE_AGENT_PROMPT.format(
            current_plan_json=json.dumps(current_plan, ensure_ascii=False, indent=2),
            user_feedback=user_feedback,
            locks_json=json.dumps(locks_payload, ensure_ascii=False, indent=2),
        )

        def _revise_once() -> TripPlan:
            response = self.create_planner_agent().run(prompt)
            return self._parse_response(response, request=None)

        new_plan_obj, _, _ = run_with_retry(
            _revise_once,
            max_retries=settings.agent_max_retries,
            backoff_seconds=settings.agent_retry_backoff_seconds,
            retry_categories={"timeout", "rate_limit", "network", "agent_error", "validation"},
        )
        return new_plan_obj.model_dump() if hasattr(new_plan_obj, "model_dump") else new_plan_obj.dict()

    def repair_trip_plan(self, plan: TripPlan, request: TripRequest, issues: list[Dict[str, Any]]) -> TripPlan:
        """依据 Validator 报告做一次最小修正，由 Orchestrator 控制循环次数。"""
        settings = get_settings()
        constraints = request.constraints.model_dump() if hasattr(request.constraints, "model_dump") else request.constraints.dict()
        current_plan = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        prompt = VALIDATION_REPAIR_PROMPT.format(
            constraints_json=json.dumps(constraints, ensure_ascii=False, indent=2),
            issues_json=json.dumps(issues, ensure_ascii=False, indent=2),
            current_plan_json=json.dumps(current_plan, ensure_ascii=False, indent=2),
        )

        def _repair_once() -> TripPlan:
            response = self.create_planner_agent().run(prompt)
            return self._parse_response(response, request=None)

        repaired, _, _ = run_with_retry(
            _repair_once,
            max_retries=settings.agent_max_retries,
            backoff_seconds=settings.agent_retry_backoff_seconds,
            retry_categories={"timeout", "rate_limit", "network", "agent_error", "validation"},
        )
        return repaired

    def _build_attraction_query(self, request: TripRequest) -> str:
        keywords = request.preferences[0] if request.preferences else "景点"
        tool_call = {
            "action": "call_tool",
            "tool_name": "maps_text_search",
            "arguments": {"keywords": keywords, "city": request.city},
        }
        return (
            f"请通过 amap MCP 网关搜索{request.city}的{keywords}相关景点。\n"
            f"[TOOL_CALL:amap:{json.dumps(tool_call, ensure_ascii=False)}]"
        )

    def _build_planner_query(
        self,
        request: TripRequest,
        attractions: str,
        weather: str,
        hotels: str = "",
        local_knowledge: str = "",
    ) -> str:
        constraints = request.constraints.model_dump() if hasattr(request.constraints, "model_dump") else request.constraints.dict()
        query = f"""请根据以下信息生成 {request.city} 的 {request.travel_days} 天旅行计划。

基本信息：
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

结构化硬约束（必须遵守）：
{json.dumps(constraints, ensure_ascii=False, indent=2)}

景点工具结果：
{attractions}

天气工具结果：
{weather}

酒店工具结果：
{hotels}

Local Knowledge Agent 核验结果：
{local_knowledge or '未提供；不要猜测景点开放/预约规则。'}

请优先按地理邻近性安排同一天的景点，并保证预算、每日游览强度和交通成本合理。
只有 Local Knowledge 的 verified_claims 可以改变具体日期安排；unverified_claims 只能作为提醒。
只返回完整 JSON。
"""
        if request.free_text_input:
            query += f"\n用户额外要求：{request.free_text_input}"
        return query

    def _parse_response(self, response: str, request: Optional[TripRequest] = None) -> TripPlan:
        try:
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "{" in response and "}" in response:
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                json_str = response[json_start:json_end]
            else:
                raise ValueError("响应中未找到JSON数据")
            return TripPlan(**json.loads(json_str))
        except Exception as exc:
            print(f"⚠️  解析响应失败: {exc}")
            if request:
                return self._create_fallback_plan(request)
            raise

    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """显式降级，不伪造景点、天气、坐标或预算。"""
        from datetime import datetime, timedelta

        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        days = []
        for index in range(request.travel_days):
            current_date = start_date + timedelta(days=index)
            days.append(DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=index,
                description="本次 Agent 执行未能生成可靠的实时行程数据，请重试。",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[],
                meals=[],
            ))

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=(
                "系统未能从 Agent / 外部工具获得足够可靠的数据，因此返回显式降级结果。"
                "请稍后重试，不建议把该结果作为真实旅行计划使用。"
            ),
            budget=None,
        )


_multi_agent_planner: Optional[MultiAgentTripPlanner] = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    global _multi_agent_planner
    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()
    return _multi_agent_planner
