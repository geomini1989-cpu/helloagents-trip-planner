# Roadmap

The core Agent workflow is already implemented. The roadmap should focus on proof, reliability, and measurable quality rather than adding more buzzword-driven features.

## Completed Core

- [x] Multi-Agent decomposition
- [x] Parallel Attraction / Weather / Hotel retrieval
- [x] Coordinator / Router intent classification
- [x] Allow-listed canonical execution graphs
- [x] Dynamic continuation flows: POI rules / weather / hotel / route / general revision
- [x] Unified `/trip/dispatch` task entry
- [x] Original TripRequest persistence for later dynamic validation
- [x] AMap MCP tool calling
- [x] Local Knowledge Agent with independent Web Search source
- [x] Source-backed opening / reservation / closure / admission-rule verification
- [x] Explicit Local Knowledge degradation when Web Search is unavailable
- [x] Local Knowledge provider/source URLs in Execution Trace
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
- [x] Live Eval workflow with LLM / AMap / Tavily secret preflight

## P0 — Validate the Current System

### Real Live Eval

Configure real credentials and run the evaluation set:

```text
LLM_API_KEY
AMAP_API_KEY
TAVILY_API_KEY
```

Goal:

- collect reproducible baseline metrics
- identify repeated failure modes
- measure real AMap route coverage
- measure GIS travel-time savings
- measure repair success
- measure Coordinator routing accuracy and unnecessary-Agent-call reduction
- measure Local Knowledge source coverage and fallback rate

Do not optimize prompts before a baseline exists.

### Dynamic Routing Eval

Add a focused routing set covering at least:

```text
full planning
POI opening/reservation rule check
weather-driven re-plan
hotel-only change
route-only optimization
general revision
ambiguous continuation request
```

Track:

- intent accuracy
- selected capability accuracy
- fallback-router rate
- unnecessary Agent/tool calls
- end-to-end latency by task type

The important question is whether Coordinator selects the smallest correct canonical graph, not whether its explanation sounds convincing.

### Local Knowledge Eval

Build a small dated evaluation set for attractions with known operating rules.

Track:

- source coverage rate
- official/first-party source rate
- no-source fallback rate
- unsupported-claim rate
- stale/conflicting-source cases
- POI-rule-driven revision success

Do not score an answer as correct merely because the LLM produced plausible opening hours. The claim must be traceable to retrieved evidence.

### Failure Case Review

For each failed case, classify the root cause:

```text
coordinator misroute
AMap retrieval failure
Local Knowledge retrieval/source failure
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

The project currently uses the HelloAgents 0.2.x `SimpleAgent` + `MCPTool` interface for AMap.

Next improvement should be stricter typed/native tool-calling semantics where supported, reducing dependence on prompt-formatted tool instructions.

Desired result:

- clearer argument schemas
- fewer malformed tool requests
- better traceability
- easier tool-level evaluation

## P1 — Local Knowledge Quality

Only after the baseline exists, consider:

- deterministic first-party / official-domain classification
- source freshness metadata
- conflicting-source detection
- page extraction for high-value sources instead of relying only on search snippets
- per-attraction source coverage
- caching short-lived operating-rule lookups

Do not automatically trust a search result simply because it ranks highly.

## P1 — Coordinator Quality

Do not add more intents until current routing is measured. After real routing Eval, consider only evidence-backed improvements such as:

- confidence / ambiguity handling
- richer task decomposition for mixed requests
- graph-level cost estimates before execution
- clarification only when two canonical graphs are genuinely indistinguishable

Do not allow the LLM to invent Python functions, arbitrary tools, or executable graph edges.

## P1 — GIS Scaling

Current exact permutation search is appropriate for small daily POI sets.

Only if larger route sets become a real requirement, consider:

- nearest-neighbor initialization
- 2-opt / local search
- time-window constraints
- verified opening-hours constraints
- hotel/start/end anchor points
- multi-modal travel costs

Do not introduce a heavier solver while the normal product limit remains about 2–3 attractions per day.

## P1 — Better Observability

Potential additions after real Eval:

- model/token usage per Agent step
- tool-call spans
- AMap request/fallback counts
- Web Search request/source counts
- first-token / end-to-end latency split
- per-case trace export
- Coordinator decision latency and selected-graph summary

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
- prepare one successful full-planning demo
- prepare one Local Knowledge reservation/closure demo with visible source URLs
- prepare one Coordinator weather re-plan demo
- prepare one route-only optimization demo showing skipped Agents
- prepare one Validator → Repair demo
- prepare one revision-lock demo
- keep one failed/degraded example to show observability

## Not a Current Priority

### A2UI

Not needed while the result UI remains structurally stable. Reconsider only if the product becomes a broader Agent workspace with dynamically selected interaction surfaces.

### More Agents

Do not add Agents just to increase Agent count. Local Knowledge was added because it owns a genuinely different source, responsibility, and failure mode. Any future Agent should meet the same bar.

### Fully Autonomous Supervisor

Not needed now. Coordinator may classify semantic intent, but the executable graph remains backend-owned. This is easier to validate, test, and explain than unrestricted autonomous orchestration.

### Generic Web Features

Login, favorites, social sharing, theme switching, and similar features are not important for demonstrating the Agent architecture.

### Multi-plan Comparison

Potential product enhancement, but lower priority than real evaluation and reliability work.

## Definition of "Portfolio Ready"

The project is ready to be presented as a strong Agent-engineering portfolio item when all of the following are true:

- offline CI passes consistently
- live Eval has a reproducible baseline
- dynamic routing Eval has a reproducible baseline
- Local Knowledge has measurable source coverage and unsupported-claim checks
- at least several failure cases have been analyzed and improved
- no benchmark number in README/resume is invented
- one end-to-end demo visibly shows Coordinator → selected task graph → multi-source retrieval / GIS / Validator → Trace
- architecture and trade-offs can be explained without relying on framework buzzwords
