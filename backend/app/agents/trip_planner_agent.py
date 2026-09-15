"""多智能体旅行规划系统。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from hello_agents import SimpleAgent
from hello_agents.tools import MCPTool

from ..config import get_settings
from ..models.schemas import DayPlan, TripPlan, TripRequest
from ..services.llm_service import get_llm
from ..services.resilience_service import run_with_retry


ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。你的任务是根据城市和用户偏好搜索合适的景点。

**重要提示:**
你必须使用工具来搜索景点!不要自己编造景点信息!

**工具调用格式:**
使用maps_text_search工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=景点关键词,city=城市名]`

**示例:**
用户: "搜索北京的历史文化景点"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]

用户: "搜索上海的公园"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=公园,city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 参数用逗号分隔
"""

WEATHER_AGENT_PROMPT = """你是天气查询专家。你的任务是查询指定城市的天气信息。

**重要提示:**
你必须使用工具来查询天气!不要自己编造天气信息!

**工具调用格式:**
使用maps_weather工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_weather:city=城市名]`

**示例:**
用户: "查询北京天气"
你的回复: [TOOL_CALL:amap_maps_weather:city=北京]

用户: "上海的天气怎么样"
你的回复: [TOOL_CALL:amap_maps_weather:city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。你的任务是根据城市和景点位置推荐合适的酒店。

**重要提示:**
你必须使用工具来搜索酒店!不要自己编造酒店信息!

**工具调用格式:**
使用maps_text_search工具搜索酒店时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=酒店,city=城市名]`

**示例:**
用户: "搜索北京的酒店"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=酒店,city=北京]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 关键词使用"酒店"或"宾馆"
"""

PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {"longitude": 116.397128, "latitude": 39.916527},
        "price_range": "300-500元",
        "rating": "4.5",
        "distance": "距离景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 120,
          "description": "景点详细描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐推荐", "description": "早餐描述", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐推荐", "description": "午餐描述", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐推荐", "description": "晚餐描述", "estimated_cost": 80}
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
```

**重要提示:**
1. weather_info数组必须包含每一天的天气信息
2. 温度必须是纯数字(不要带°C等单位)
3. 每天安排2-3个景点
4. 考虑景点之间的距离和游览时间
5. 每天必须包含早中晚三餐
6. 提供实用的旅行建议
7. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
8. day_index 必须从 0 开始计数 (0 代表第一天，1 代表第二天)
"""

REVISE_AGENT_PROMPT = """你是一个行程规划修正专家。你的任务是根据用户的【修改意见】，对【当前的JSON行程】进行局部调整。

**输入数据:**
1. 当前行程 JSON (Current Plan)
2. 用户修改意见 (User Feedback)

**要求:**
1. 只修改用户提到的部分（如：更换某天的酒店、删除某个景点、调整时间）。
2. 其他未提到的部分必须保持原样。
3. 必须输出完整的、合法的 JSON 数据（格式与原计划完全一致）。
4. 不要输出任何解释性文字，只输出 JSON 代码块。

**当前行程:**
{current_plan_json}

**用户修改意见:**
{user_feedback}
"""


class MultiAgentTripPlanner:
    """多智能体旅行规划系统。

    LLM 与 MCP 配置可以复用，但 SimpleAgent 不做跨请求单例复用。
    HelloAgents 的 SimpleAgent 会在同一实例中保留对话历史，因此每次业务请求
    创建新的 Agent，避免不同用户、不同 Eval case 之间出现上下文串扰。
    """

    def __init__(self):
        settings = get_settings()
        self.llm = get_llm()
        self.amap_tool = MCPTool(
            name="amap",
            description="高德地图服务",
            server_command=["uvx", "amap-mcp-server"],
            env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
            auto_expand=True,
        )

    def _create_tool_agent(self, *, name: str, prompt: str) -> SimpleAgent:
        agent = SimpleAgent(name=name, llm=self.llm, system_prompt=prompt)
        agent.add_tool(self.amap_tool)
        return agent

    def create_planner_agent(self) -> SimpleAgent:
        """创建无跨请求历史的 Planner Agent。"""
        return SimpleAgent(
            name="行程规划专家",
            llm=self.llm,
            system_prompt=PLANNER_AGENT_PROMPT,
        )

    def create_request_agents(self) -> Dict[str, SimpleAgent]:
        """为一次规划请求创建独立 Agent 上下文。"""
        return {
            "attraction": self._create_tool_agent(
                name="景点搜索专家",
                prompt=ATTRACTION_AGENT_PROMPT,
            ),
            "weather": self._create_tool_agent(
                name="天气查询专家",
                prompt=WEATHER_AGENT_PROMPT,
            ),
            "hotel": self._create_tool_agent(
                name="酒店推荐专家",
                prompt=HOTEL_AGENT_PROMPT,
            ),
            "planner": self.create_planner_agent(),
        }

    def plan_trip(self, request: TripRequest) -> TripPlan:
        """兼容旧调用方式的顺序执行入口；API 主流程使用 Orchestrator。"""
        try:
            agents = self.create_request_agents()
            attraction_response = agents["attraction"].run(self._build_attraction_query(request))
            weather_response = agents["weather"].run(f"请查询{request.city}的天气信息")
            hotel_response = agents["hotel"].run(f"请搜索{request.city}的{request.accommodation}酒店")
            planner_query = self._build_planner_query(
                request,
                attraction_response,
                weather_response,
                hotel_response,
            )
            planner_response = agents["planner"].run(planner_query)
            return self._parse_response(planner_response, request)
        except Exception as exc:
            print(f"❌ 生成旅行计划失败: {exc}")
            return self._create_fallback_plan(request)

    def revise_trip(self, current_plan: Dict[str, Any], user_feedback: str) -> Dict[str, Any]:
        """基于当前计划做局部修改；失败时抛出异常，不伪装成成功。"""
        settings = get_settings()
        prompt = REVISE_AGENT_PROMPT.format(
            current_plan_json=json.dumps(current_plan, ensure_ascii=False, indent=2),
            user_feedback=user_feedback,
        )
        revision_agent = self.create_planner_agent()

        def _revise_once() -> TripPlan:
            response = revision_agent.run(prompt)
            return self._parse_response(response, request=None)

        new_plan_obj, _, _ = run_with_retry(
            _revise_once,
            max_retries=settings.agent_max_retries,
            backoff_seconds=settings.agent_retry_backoff_seconds,
            retry_categories={"timeout", "rate_limit", "network", "agent_error", "validation"},
        )
        return new_plan_obj.model_dump() if hasattr(new_plan_obj, "model_dump") else new_plan_obj.dict()

    def _build_attraction_query(self, request: TripRequest) -> str:
        """构建景点搜索查询。"""
        keywords = request.preferences[0] if request.preferences else "景点"
        return (
            f"请使用amap_maps_text_search工具搜索{request.city}的{keywords}相关景点。\n"
            f"[TOOL_CALL:amap_maps_text_search:keywords={keywords},city={request.city}]"
        )

    def _build_planner_query(
        self,
        request: TripRequest,
        attractions: str,
        weather: str,
        hotels: str = "",
    ) -> str:
        """构建行程规划查询。"""
        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**酒店信息:**
{hotels}

**要求:**
1. 每天安排2-3个景点
2. 每天必须包含早中晚三餐
3. 每天推荐一个具体的酒店(从酒店信息中选择)
4. 考虑景点之间的距离和交通方式
5. 返回完整的JSON格式数据
6. 景点的经纬度坐标要来自可靠信息，不要编造不存在的地点
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"
        return query

    def _parse_response(self, response: str, request: Optional[TripRequest] = None) -> TripPlan:
        """解析 Agent JSON 响应并交给 Pydantic 做结构校验。"""
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
        """创建显式降级结果，不伪造景点、天气、坐标或预算。"""
        from datetime import datetime, timedelta

        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        days = []
        for index in range(request.travel_days):
            current_date = start_date + timedelta(days=index)
            days.append(
                DayPlan(
                    date=current_date.strftime("%Y-%m-%d"),
                    day_index=index,
                    description="本次 Agent 执行未能生成可靠的实时行程数据，请重试。",
                    transportation=request.transportation,
                    accommodation=request.accommodation,
                    attractions=[],
                    meals=[],
                )
            )

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=(
                "系统本次未能从 Agent / 外部工具获得足够可靠的数据，因此返回显式降级结果。"
                "请稍后重试，不建议把该结果作为真实旅行计划使用。"
            ),
            budget=None,
        )


_multi_agent_planner: Optional[MultiAgentTripPlanner] = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """复用系统配置对象；具体 SimpleAgent 在每次请求内重新创建。"""
    global _multi_agent_planner
    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()
    return _multi_agent_planner
