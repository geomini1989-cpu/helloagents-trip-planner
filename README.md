# Multi-Agent Trip Planner

一个面向真实旅行规划场景的 **Multi-Agent AI Application**。

项目基于 HelloAgents 构建专职 Agent，通过 **MCP（Model Context Protocol）** 接入高德地图能力，并使用 FastAPI + Vue 3 完成任务编排、真实工具调用、GIS 路线优化、结构化约束、确定性校验、自动修正、会话持久化、局部修改保护、执行追踪与 Agent Eval。

> 重点不是“让大模型生成一段旅行文案”，而是让 Agent 在 **真实工具、空间数据、硬约束、确定性规则和反馈闭环** 下完成旅行规划任务。

## Highlights

- **Multi-Agent Decomposition**：Attraction / Weather / Hotel / Planner 职责拆分
- **Parallel Orchestration**：检索 Agent 使用 fan-out / fan-in 并行执行
- **MCP Tool Calling**：通过高德地图 MCP 获取 POI、天气和路线数据
- **Structured Constraints**：预算、每日景点数、游览时长、单段交通时长转为硬约束
- **Natural-language Constraint Extraction**：支持“预算 2000 元以内”“每天最多 2 个景点”等自然语言要求
- **GIS Route Optimizer**：将景点经纬度与高德路网时间转换为成本矩阵，确定性优化每日访问顺序
- **Network + Spatial Fallback**：真实路网不可用时使用 Haversine 空间距离估算，并显式记录数据来源
- **Deterministic Trip Validator**：检查预算、每日强度、重复景点、路线耗时等可明确判断的问题
- **Validate → Repair → GIS → Revalidate**：初稿不合格时触发一次最小自动修正，重新优化路线后再次校验
- **Persistent Revision Locks**：支持锁定日期、酒店和指定景点，锁定状态持久化到 Session
- **Deterministic Lock Guard**：即使 Revision Agent 误改锁定内容，后端也会检测、恢复并写入 Trace
- **Reliability Policy**：异常分类、有限重试、退避与显式 fallback
- **Request-scoped Agents**：避免 SimpleAgent history 跨请求污染
- **Persistent Session**：SQLite 保存计划、历史版本、Revision Locks 和 Execution Trace
- **Observability**：Trace 页面展示 Agent / Tool / Retry / GIS / Validator / Repair / Lock Guard / Latency
- **Deterministic Eval**：衡量生成成功率、工具成功率、验证通过率、修正成功率和延迟
- **CI**：自动执行 Python compile、单元测试和 Vue/TypeScript build

## Architecture

```mermaid
flowchart TD
    U[User] --> API[FastAPI]
    API --> C[Constraint Normalizer]
    C --> O[Orchestrator]

    O -->|parallel| A[Attraction Agent]
    O -->|parallel| W[Weather Agent]
    O -->|parallel| H[Hotel Agent]

    A --> MCP[AMap MCP]
    W --> MCP
    H --> MCP

    A --> P[Planner Agent]
    W --> P
    H --> P
    C --> P

    P --> T[Typed TripPlan]
    T --> GIS[GIS Route Optimizer]
    GIS --> RM[AMap Route Matrix / Haversine]
    GIS --> V[Deterministic Validator]

    V -->|passed| DB[(SQLite Session Store)]
    V -->|blocking issues| R[Repair Agent]
    R --> GIS2[Post-Repair GIS Optimizer]
    GIS2 --> V2[Post-Repair Validator]
    V2 --> DB

    DB --> REV[Revision Agent]
    REV --> LG[Revision Lock Guard]
    LG --> DB

    O --> TRACE[Execution Trace]
    TRACE --> DB
    DB --> FE[Vue Result / Trace UI]
```

## Planning Workflow

```text
User Request
    ↓
Constraint Normalizer
    ↓
Orchestrator
    ├── Attraction Agent ── AMap MCP ─┐
    ├── Weather Agent ───── AMap MCP ─┼── parallel
    └── Hotel Agent ─────── AMap MCP ─┘
                    ↓ fan-in
               Planner Agent
                    ↓
               Typed TripPlan
                    ↓
            GIS Route Optimizer
                    ↓
              Trip Validator
        ┌───────────┴───────────┐
      passed                blocking issues
        ↓                         ↓
   Final Plan                Repair Agent
                                  ↓
                         Post-Repair GIS
                                  ↓
                         Post-Repair Validator
                                  ↓
                              Final Plan
```

自动 Repair 最多执行一次，避免 Agent 在“生成 → 校验 → 重写”之间无限循环。

## GIS Route Optimization

旅行计划里“去哪些地方”和“按什么顺序去”不是同一类问题。

本项目让 LLM 负责语义规划，而把路线排序交给确定性 GIS / 路网层：

```text
Planner 选择景点集合
        ↓
经纬度 Geo Points
        ↓
Directed Cost Matrix
   ├─ AMap Route Time
   └─ Haversine Fallback
        ↓
Exact Route Search
        ↓
更低交通成本的访问顺序
```

对于常见的每天 2–3 个景点，候选数量很小，因此直接枚举所有访问顺序，选择总交通时间最低的顺序，而不是让 LLM 猜测“哪个景点离哪个更近”。

例如原始计划：

```text
A → B → C
预计交通 120 min
```

路网成本矩阵发现：

```text
A → C → B
预计交通 20 min
```

则 GIS Optimizer 会确定性重排，并在 Trace 记录：

```text
original_order
optimized_order
before_minutes
after_minutes
saved_minutes
source_counts
evaluated_permutations
```

为避免景点数量异常时产生大量 MCP 调用：

- 每日景点数 ≤ 5：优先构建真实 AMap 路网成本矩阵
- 每日景点数 > 5：使用坐标空间距离估算做排序
- 最终 Validator 仍会对实际相邻路段进行路线校验

当前默认硬约束每天最多 3 个景点，因此正常场景通常会使用真实路网矩阵。

## Structured Constraints

API 可以直接传递结构化约束：

```json
{
  "city": "北京",
  "start_date": "2026-10-01",
  "end_date": "2026-10-03",
  "travel_days": 3,
  "transportation": "公共交通",
  "accommodation": "舒适型酒店",
  "preferences": ["历史文化", "美食"],
  "constraints": {
    "max_budget": 2500,
    "max_daily_attractions": 3,
    "max_daily_visit_minutes": 480,
    "max_route_minutes": 60
  }
}
```

普通用户也可以直接写：

```text
总预算控制在 2500 元以内，每天最多 3 个景点，单段交通不要超过 45 分钟。
```

后端只提取能明确量化的要求，不会把模糊偏好强行转换成硬约束。

## Deterministic Validator

Validator 当前检查：

- 行程天数与请求是否一致
- 总预算是否超过上限
- 每天景点数量是否过多
- 每日景点游览总时长是否过高
- 是否重复安排同一景点
- GIS 优化后的相邻景点交通耗时是否超过限制
- 路线数据来自真实 AMap MCP 还是坐标估算 fallback

与单纯让另一个 LLM “自己检查一下”不同，这些规则可以重复执行、自动测试，也可以进入 Eval。

## Validate → Repair → GIS → Revalidate

Planner 初稿出现硬冲突时，Repair Agent 会获得：

```text
当前 TripPlan
+ Structured Constraints
+ Validator Blocking Issues
```

Repair Prompt 明确要求 **最小范围修改**，而不是重新自由规划整个行程。

由于 Repair 可能改变景点集合或顺序，修正后不是直接返回，而是再次执行：

```text
Repair Agent
    ↓
GIS Route Optimizer
    ↓
Post-Repair Validator
```

如果仍然不通过，Orchestrator 会标记：

```text
status = degraded
validation_passed = false
```

系统不会把未解决的冲突伪装成完全成功。

## Revision Locks

自然语言局部修改支持持久化锁定，例如：

```text
第一天和酒店已经确定，不要改，把第三天改轻松一点。
```

后端会解析成类似：

```json
{
  "locked_day_indexes": [0],
  "lock_all_hotels": true,
  "locked_attraction_names": []
}
```

Revision Agent 在 Prompt 中会收到这些锁，但系统不会只相信模型遵守规则。

修改完成后，`Revision Lock Guard` 会确定性比较修改前后数据：

```text
Original Plan
     +
Revision Candidate
     +
Persistent Locks
     ↓
Revision Lock Guard
     ↓
restore locked fields if changed
```

如果 Agent 误改了锁定内容：

1. 后端恢复原值；
2. 修改其他未锁定部分仍然保留；
3. violation 写入 Execution Trace；
4. 锁定状态继续保存在 SQLite Session 中。

也可以显式调用：

```text
PUT /api/trip/session/{session_id}/locks
```

支持锁定：

- 指定日期
- 所有酒店 / 住宿
- 指定景点

## Reliability

统一异常类别：

```text
timeout
rate_limit
network
auth
validation
agent_error
```

默认：

```env
AGENT_MAX_RETRIES=2
AGENT_RETRY_BACKOFF_SECONDS=0.5
```

- timeout / network / rate limit：允许有限重试
- auth：不重试
- Planner JSON / Pydantic validation：允许重新生成
- 所有尝试耗尽后才进入 fallback

Fallback 不会伪造景点、天气或坐标，而是返回明确的降级结果。

## Execution Trace

一次完整生成请求可以包含：

```text
Attraction Agent ┐
Weather Agent    ├── parallel
Hotel Agent      ┘
       ↓
Planner Agent
       ↓
GIS Route Optimizer
       ↓
Trip Validator
       ↓ (if needed)
Repair Agent
       ↓
Post-Repair GIS Optimizer
       ↓
Post-Repair Validator
       ↓
Orchestrator
```

局部修改时还会追加：

```text
Revision Agent
      ↓
Revision Lock Guard
```

事件可包含：

```text
status
latency
attempts
retry history
error category
tool name
validation issues
route source
route optimization before/after
lock violations
degraded state
```

## Agent Eval

运行：

```bash
cd backend
python -m evals.run_eval
```

只跑前三条：

```bash
python -m evals.run_eval --limit 3
```

当前聚合指标包括：

```text
case_pass_rate
average_check_score
structured_output_success_rate
retrieval_agent_success_rate
validation_pass_rate
repair_trigger_rate
repair_success_rate
fallback_rate
agent_step_retry_rate
average_latency_ms
p95_latency_ms
```

README 不预填任何虚构成绩；只有真实环境完成 Eval 后才应该把指标写进简历。

## Persistent Session

SQLite 保存：

```text
session_id
current_plan
history
execution_trace
locks
created_at
updated_at
```

旧版本进入 `history`，Revision / Lock Guard Trace 追加到 Session，锁定状态跨页面刷新和服务重启仍可恢复。

## Tech Stack

### Agent / Backend

- Python
- HelloAgents / SimpleAgent
- MCPTool / Model Context Protocol
- AMap MCP Server
- FastAPI
- Pydantic
- SQLite
- GIS / Haversine distance
- Exact route permutation search
- ThreadPoolExecutor
- pytest

### Frontend

- Vue 3
- TypeScript
- Vue Router
- Vite
- Ant Design Vue
- AMap JavaScript API

## Project Structure

```text
multi-agent-trip-planner/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   └── trip_planner_agent.py
│   │   ├── api/routes/
│   │   │   └── trip.py
│   │   ├── models/
│   │   │   └── schemas.py
│   │   └── services/
│   │       ├── orchestration_service.py
│   │       ├── constraint_service.py
│   │       ├── gis_optimizer_service.py
│   │       ├── route_service.py
│   │       ├── validation_service.py
│   │       ├── revision_lock_service.py
│   │       ├── resilience_service.py
│   │       └── session_service.py
│   ├── evals/
│   │   ├── cases.json
│   │   └── run_eval.py
│   └── tests/
├── frontend/
│   └── src/views/
│       ├── Home.vue
│       ├── Result.vue
│       └── Trace.vue
└── .github/workflows/ci.yml
```

## Quick Start

Backend：

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend：

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Engineering Decisions

### Why GIS route optimization instead of asking the LLM to sort POIs?

LLM 擅长理解旅行偏好、景点语义和用户意图，但路程距离、路网耗时和访问顺序属于可以明确计算的空间优化问题。

因此当前职责拆分是：

```text
LLM       → what to visit
GIS/MCP   → travel cost
Algorithm → visit order
Validator → whether constraints are satisfied
```

相比让模型根据地名猜距离，这种方式更可解释、可测试，也能直接量化优化前后的交通成本。

### Why deterministic validation instead of another Reviewer LLM?

预算、数量、耗时、重复景点、路线时长等问题都有可明确判断的规则。

对于这些问题，确定性 Validator 更：

- 可重复
- 可解释
- 可测试
- 可进入 CI / Eval
- 不增加额外 Judge 模型的不确定性

LLM 负责生成与修正，代码负责判断明确约束是否满足。

### Why deterministic Lock Guard?

在 Prompt 中告诉 Agent “不要改第一天”属于软约束，不能证明模型一定遵守。

因此系统采用：

```text
Prompt-aware Revision
        +
Deterministic Post-check
```

Agent 可以负责理解用户修改意图，但最终锁定数据是否被改动由代码比较和恢复。

### Why only one automatic repair?

无限 Planner ↔ Reviewer 循环会增加成本、延迟和不可预测性。

当前策略是：

```text
Generate → GIS → Validate → one Repair → GIS → Revalidate
```

如果仍不通过，就显式 degraded，而不是继续无限调用模型。

### Why not A2UI now?

当前 UI 的核心结构稳定：行程、天气、酒店、预算、地图、Trace。

这里主要是 **业务数据动态**，不是 **UI 结构动态**，因此当前选择：

```text
Typed TripPlan
    ↓
Deterministic Vue Components
```

当项目未来演进成通用 Travel Agent Workspace，需要根据任务动态产生 Comparison Table、Approval Form、Budget Editor 等不同 Surface 时，再引入 A2UI 更合理。

## Current Boundaries

- Tool Calling 仍依赖当前 HelloAgents 的调用约定，后续可升级 typed/native tool calling
- AMap route MCP 输出存在版本差异，因此保留 Haversine fallback
- GIS Optimizer 当前重点优化“每天景点之间”的访问顺序，尚未把酒店→首站→末站→酒店作为完整闭环路径优化
- 每日 > 5 个景点时不会构建完整路网矩阵，以避免 O(n²) 外部工具调用；正常默认约束为每天最多 3 个景点
- Agent 路由仍是固定 DAG，不是动态 Coordinator
- Session Store 使用 SQLite，尚未面向多实例部署
- Eval 以确定性规则为主，尚未加入主观旅行体验 rubric
- Trace 尚未记录完整 token usage / LLM span

## Roadmap

- [x] Multi-Agent decomposition
- [x] MCP tool calling
- [x] Parallel fan-out / fan-in orchestration
- [x] Structured TripPlan
- [x] Request-scoped Agent isolation
- [x] Retry / backoff / error classification
- [x] SQLite session persistence
- [x] Execution Trace UI
- [x] Deterministic Agent Eval
- [x] Structured constraints
- [x] Natural-language constraint extraction
- [x] Route-aware deterministic Validator
- [x] Validate → Repair → Revalidate loop
- [x] GIS / network-cost route ordering
- [x] Persistent revision locks
- [x] Deterministic Lock Guard
- [x] CI
- [ ] Typed / native tool calling
- [ ] Hotel-anchored full-day route optimization
- [ ] Token / LLM span tracing
- [ ] Docker / deployment
- [ ] Dynamic Coordinator / Router

## License

CC BY-NC-SA 4.0

## Acknowledgements

- Hello-Agents
- HelloAgents
- 高德地图开放平台
- amap-mcp-server
