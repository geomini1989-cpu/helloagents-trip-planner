# Roadmap

The core Agent workflow is already implemented. The roadmap should therefore focus on proof, reliability, and product completeness rather than adding more buzzword-driven features.

## Completed Core

- [x] Multi-Agent decomposition
- [x] Parallel retrieval orchestration
- [x] AMap MCP tool calling
- [x] Typed TripPlan output
- [x] Structured hard constraints
- [x] Natural-language constraint extraction
- [x] GIS route-cost optimization
- [x] AMap route + Haversine fallback
- [x] Deterministic trip validation
- [x] One-shot automatic repair
- [x] Post-repair GIS + revalidation
- [x] SQLite session persistence
- [x] Persistent revision locks
- [x] Deterministic revision lock guard
- [x] Retry/backoff/error classification
- [x] Request-scoped Agent instances
- [x] Execution Trace UI
- [x] Deterministic Agent Eval
- [x] Offline CI
- [x] Live Eval workflow with secret preflight

## P0 — Validate the Current System

### Real Live Eval

Configure real model and AMap credentials and run the evaluation set.

Goal:

- collect reproducible baseline metrics
- identify repeated failure modes
- measure real AMap route coverage
- measure GIS travel-time savings
- measure repair success

Do not optimize prompts before a baseline exists.

### Failure Case Review

For each failed case, classify the root cause:

```text
retrieval failure
LLM planning failure
structured output failure
route data failure
constraint extraction failure
validator false positive/negative
repair regression
latency/retry issue
```

Turn recurring failures into regression tests where possible.

## P1 — Tool Calling Quality

The project currently uses the HelloAgents 0.2.x `SimpleAgent` + `MCPTool` interface.

Next improvement should be stricter typed/native tool-calling semantics where supported, reducing dependence on prompt-formatted tool instructions.

Desired result:

- clearer argument schemas
- fewer malformed tool requests
- better traceability
- easier tool-level evaluation

## P1 — GIS Scaling

Current exact permutation search is appropriate for small daily POI sets.

Only if larger route sets become a real requirement, consider:

- nearest-neighbor initialization
- 2-opt / local search
- time-window constraints
- opening-hours constraints
- hotel/start/end anchor points
- multi-modal travel costs

Do not introduce a heavier solver while the normal product limit remains about 2–3 attractions per day.

## P1 — Better Observability

Potential additions after real Eval:

- model/token usage per Agent step
- tool-call spans
- AMap request/fallback counts
- first-token / end-to-end latency split
- per-case trace export

These are more useful than adding more Agent roles.

## P2 — Deployment

Once the local system is stable:

- Dockerize backend/frontend
- add a production configuration template
- add persistent volume guidance for SQLite
- define health/readiness checks
- document deployment limits

For multi-instance production deployment, SQLite should eventually be replaced or moved behind a proper shared persistence layer.

## P2 — Demo Quality

Before using the project heavily in interviews:

- add 2–3 screenshots or a short GIF
- prepare one successful planning demo
- prepare one Validator → Repair demo
- prepare one GIS route optimization demo
- prepare one revision-lock demo
- keep one failed/degraded example to show observability

## Not a Current Priority

### A2UI

Not needed while the result UI remains structurally stable. Reconsider only if the product becomes a broader Agent workspace with dynamically selected interaction surfaces.

### More Agents

Do not add Agents just to increase Agent count. Add a new Agent only when it owns a genuinely different source of data, tool boundary, or reasoning responsibility.

### Generic Web Features

Login, favorites, social sharing, theme switching, and similar features are not important for demonstrating the Agent architecture.

### Multi-plan Comparison

Potential product enhancement, but lower priority than live evaluation and reliability work.

## Definition of "Portfolio Ready"

The project is ready to be presented as a strong Agent-engineering portfolio item when all of the following are true:

- offline CI passes consistently
- live Eval has a reproducible baseline
- at least several failure cases have been analyzed and improved
- no benchmark number in README/resume is invented
- one end-to-end demo can visibly show MCP → planning → GIS → validation → repair → trace
- architecture and trade-offs can be explained without relying on framework buzzwords