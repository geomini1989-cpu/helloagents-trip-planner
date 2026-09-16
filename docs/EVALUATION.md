# Evaluation & Testing

## 1. Evaluation Layers

The project now separates evaluation into three layers so that failures can be attributed to the right part of the Agent system.

### Offline CI

Runs automatically on pushes and pull requests:

- Python source compilation
- backend unit tests with pytest
- frontend TypeScript/Vue build
- Coordinator canonical-graph safety tests
- Local Knowledge citation-grounding tests
- routing-eval metric tests

These checks do not require live external credentials.

### Coordinator Routing Eval

Evaluates only the Coordinator / Router. It does not call AMap, Tavily, GIS or Planner.

Its purpose is to answer:

```text
Did the Coordinator classify the intent correctly?
Did it select the exact canonical capability graph?
Did it call unnecessary Agents/capabilities?
Did it omit required capabilities?
Did it fall back to the deterministic router?
```

The current routing set covers:

```text
full_plan
weather_replan
hotel_change
route_optimize
general_revision
poi_rules_check
```

### Full Live Agent Eval

Runs the actual planning pipeline with real model, AMap and Local Knowledge web retrieval.

The travel cases cover Beijing, Shanghai, Guangzhou, Chengdu, Xi'an, Hangzhou, Nanjing and Shenzhen, including explicit budget, attraction-count, visit-time and route-time constraints.

## 2. Local Commands

Run the full trip evaluation:

```bash
cd backend
python -m evals.run_eval
```

Run only the first three full cases:

```bash
python -m evals.run_eval --limit 3
```

Run Coordinator routing evaluation only:

```bash
python -m evals.run_routing_eval
```

Run selected routing cases:

```bash
python -m evals.run_routing_eval --case route-reorder --case poi-reservation-rule
```

## 3. Full-Flow Metrics

`evals.run_eval` currently reports:

```text
case_pass_rate
average_check_score
structured_output_success_rate
retrieval_agent_success_rate

local_knowledge_source_coverage_rate
local_knowledge_degraded_rate
local_knowledge_schema_valid_rate
local_knowledge_supported_claim_rate
local_knowledge_unsupported_claim_rate
local_knowledge_unverified_claim_rate

validation_pass_rate
repair_trigger_rate
repair_success_rate
fallback_rate
agent_step_retry_rate
average_latency_ms
p95_latency_ms
```

The Local Knowledge metrics are deterministic. A claim marked `verified` by the LLM is counted as supported only if its `source_url` exactly matches a URL returned by the current Web Search call.

If the Agent invents a URL, the backend changes the claim to `unsupported` and removes it from the facts passed to Planner / Revision. Therefore `local_knowledge_unsupported_claim_rate` measures model grounding mistakes without requiring an LLM-as-a-Judge.

## 4. Coordinator Routing Metrics

`evals.run_routing_eval` reports:

```text
intent_accuracy
capability_exact_match_rate
minimal_graph_rate
fallback_router_rate
unnecessary_capability_rate
missing_capability_rate
unnecessary_agent_call_rate
average_selected_capabilities
average_capability_reduction_vs_full_plan
```

### Intent accuracy

Whether the request was routed to the expected intent.

### Capability exact match rate

Whether the selected backend-owned capability sequence exactly matches the expected canonical graph.

### Minimal graph rate

Whether the graph has neither extra nor missing capabilities.

### Unnecessary Agent call rate

How often the selected graph includes an Agent capability that was not required by the reference case.

This metric is especially useful for demonstrating that dynamic orchestration is doing more than dispatching every request through the full planning pipeline.

### Capability reduction vs full-plan graph

A descriptive cost-efficiency metric comparing the selected graph size with the eight-capability full-plan graph. It should not be interpreted as quality by itself; selecting too few capabilities can also be wrong, which is why exact-match and missing-capability metrics are reported separately.

## 5. Local Knowledge Grounding

Local Knowledge follows a two-stage trust model:

```text
Tavily Web Search
      ↓
Candidate source URLs
      ↓
Local Knowledge Agent
      ↓
Structured claims
      ↓
Deterministic citation check
```

Each claim contains:

```text
attraction
claim_type
claim
verification_status
source_url
```

Allowed statuses are:

```text
verified
unverified
unsupported
```

A `verified` claim whose `source_url` is not in the current search result set is automatically converted to `unsupported`.

Only `verified_claims` are passed downstream as operational facts. `unverified_claims` can be shown as reminders. `unsupported` claims are retained in Trace/Eval for debugging but are filtered out of Planner context.

## 6. What A Passing Full Case Means

A full case checks multiple dimensions, including:

- city and day count match the request
- day indexes are sequential
- expected attraction coverage is present
- meals are complete
- coordinates are valid
- weather is present when required
- budget is present and internally consistent when required
- Attraction / Weather / Hotel retrieval succeeded
- Planner produced structured output
- Local Knowledge emitted no unsupported source-backed claims
- deterministic Validator passed after any repair
- no pipeline fallback was used

The goal is not to reward verbose prose. The goal is to measure whether the system completed the task under explicit constraints with traceable external information.

## 7. GIS Evaluation

GIS quality should be separated from LLM quality.

Useful measurements include:

```text
route_optimizer_trigger_rate
average_minutes_saved
median_minutes_saved
AMap route coverage
Haversine fallback rate
post-GIS route constraint pass rate
```

Only real AMap runs should be used when claiming real-world travel-time savings. Coordinate fallback estimates must not be presented as measured AMap travel time.

## 8. Repair Evaluation

Repair should answer three questions:

1. How often does the first plan violate deterministic constraints?
2. When repair is triggered, how often are blocking issues removed?
3. Does repair introduce new problems?

Recommended breakdown:

```text
initial_validation_pass_rate
repair_trigger_rate
repair_success_rate
post_repair_degraded_rate
```

## 9. Revision Lock Evaluation

Lock behavior remains deterministic because it should not depend on model judgment.

Important cases include:

- locked day remains unchanged
- locked hotel remains unchanged
- locked attraction remains unchanged
- unrelated unlocked content can still change
- mixed natural-language lock instructions do not over-lock unrelated days
- lock violations are recorded in Trace

## 10. GitHub Actions

### CI

Workflow:

```text
.github/workflows/ci.yml
```

Runs offline engineering checks.

### Live Agent Eval

Workflow:

```text
.github/workflows/live-eval.yml
```

The workflow now has two independent preflight levels:

```text
LLM_API_KEY
→ Coordinator Routing Eval can run

LLM_API_KEY + AMAP_API_KEY + TAVILY_API_KEY
→ Full Live Agent Eval can run
```

This means routing quality can be measured even when map/search credentials are not configured.

Artifacts:

```text
coordinator-routing-eval-report
live-agent-eval-report
```

The workflow skips unavailable live sections explicitly instead of reporting mock or empty execution as a real benchmark.

## 11. Credentials

Typical live-eval requirements are:

```text
LLM_API_KEY
AMAP_API_KEY
TAVILY_API_KEY
```

Local examples are documented in `backend/.env.example`. Never commit real credentials into the repository.

## 12. Resume / Interview Usage

Only publish numbers after a reproducible real run.

Potential metrics worth using after measurement include:

```text
Coordinator Intent Accuracy
Minimal Graph Rate
Unnecessary Agent Call Rate
Local Knowledge Source Coverage
Unsupported Claim Rate
Structured Output Success Rate
Constraint Validation Pass Rate
Repair Success Rate
Average GIS Travel-Time Reduction
P95 End-to-End Latency
```

Do not use placeholder percentages. A metric belongs in README or a resume only after the report artifact exists and can be reproduced.
