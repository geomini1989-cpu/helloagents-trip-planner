"""旅行规划API路由"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    # ErrorResponse (如果没用到可以去掉)
)
from ...agents.trip_planner_agent import get_trip_planner_agent
# 引入我们在上一步创建的 session 服务
from ...services.session_service import create_session, get_session, update_session_plan

router = APIRouter(prefix="/trip", tags=["旅行规划"])

# ==========================================
# ✨ 新增数据模型
# ==========================================

# 定义修改请求的数据结构
class ReviseRequest(BaseModel):
    session_id: str
    feedback: str  # 用户输入的修改意见

# 定义通用的响应结构 (包含 session_id)
# 为了不修改您的 schemas.py，我们在这里定义一个临时的响应模型，或者直接返回字典
class TripResponseWithSession(BaseModel):
    success: bool
    message: str
    session_id: str
    data: Any  # 这里放 TripPlan 数据

# ==========================================
# 🚀 核心路由
# ==========================================

@router.post(
    "/plan",
    response_model=TripResponseWithSession, # ✨ 修改响应模型，包含 session_id
    summary="生成旅行计划",
    description="根据用户输入生成初始计划，并开启会话"
)
async def plan_trip(request: TripRequest):
    """
    生成旅行计划
    """
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"{'='*60}\n")

        # 1. 获取Agent
        agent = get_trip_planner_agent()

        # 2. 生成计划
        print("🚀 开始生成旅行计划...")
        trip_plan = agent.plan_trip(request)

        # 3. ✨ 创建会话 (保存当前计划)
        # 兼容 Pydantic v1/v2 写法
        plan_dict = trip_plan.model_dump() if hasattr(trip_plan, 'model_dump') else trip_plan.dict()
        session_id = create_session(plan_dict)

        print(f"✅ 计划生成成功! Session ID: {session_id}\n")

        # 4. ✨ 返回包含 session_id 的响应
        return TripResponseWithSession(
            success=True,
            message="旅行计划生成成功",
            session_id=session_id,
            data=trip_plan
        )

    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.post(
    "/revise",
    response_model=TripResponseWithSession, # 复用上面的响应模型
    summary="修改旅行计划",
    description="根据用户的自然语言反馈修改现有计划"
)
async def revise_trip(request: ReviseRequest):
    """
    修改旅行计划 (多轮对话核心接口)
    """
    try:
        print(f"\n{'='*60}")
        print(f"🔄 收到修改请求 (Session: {request.session_id})")
        print(f"📝 用户反馈: {request.feedback}")
        
        # 1. 找回旧计划
        session = get_session(request.session_id)
        if not session:
            print("❌ Session 不存在或已过期")
            raise HTTPException(status_code=404, detail="会话已过期，请重新生成计划")
        
        current_plan = session["current_plan"]
        
        # 2. 调用 Agent 进行修改
        agent = get_trip_planner_agent()
        # 调用我们在 agent 中新增的 revise_trip 方法
        new_plan_dict = agent.revise_trip(current_plan, request.feedback)
        
        # 3. 更新会话中的数据
        update_session_plan(request.session_id, new_plan_dict)
        
        print("✅ 修改完成，返回新计划\n")
        
        # 4. 返回结果
        return TripResponseWithSession(
            success=True,
            message="行程修改成功",
            session_id=request.session_id,
            data=new_plan_dict
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ 修改失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500, 
            detail=f"修改失败: {str(e)}"
        )


@router.get("/health")
async def health_check():
    """健康检查"""
    try:
        agent = get_trip_planner_agent()
        return {
            "status": "healthy",
            "service": "trip-planner",
            "model": "qwen-plus" # 或者您可以动态获取 model name
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"服务不可用: {str(e)}")