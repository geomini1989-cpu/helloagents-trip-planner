# Evaluation & Testing

## 1. What Is Tested

The project separates deterministic engineering tests from live Agent evaluation.

### Offline CI

Runs automatically on pushes and pull requests:

- Python source compilation
- backend unit tests with pytest
- frontend TypeScript/Vue build

These tests do not require live LLM or AMap credentials.

### Live Agent Eval

Runs the actual planning pipeline with real model and AMap access. It is intended to measure system behavior rather than only code correctness.

The current evaluation set contains scenarios for cities such as Beijing, Shanghai, Guangzhou, Chengdu, Xi'an, Hangzhou, Nanjing, and Shenzhen. Cases include explicit budget, attraction-count, visit-time, and route-time constraints.

## 2. Local Eval

Run all cases:

```bash
cd backend
python -m evals.run_eval
```

Run only the first three cases:

```bash
python -m evals.run_eval --limit 3
```

## 3. Metrics

Current aggregate metrics include:

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

These metrics are intentionally deterministic where possible. The repository should not publish invented benchmark numbers before running against a real environment.

## 4. What A Passing Case Means

A case can check several dimensions:

- the pipeline returned a structured `TripPlan`
- required trip days were produced
- attraction coverage is sufficient
- budget information is present when required
- weather information is present when required
- retrieval Agents completed successfully
- deterministic Validator did not leave unresolved blocking issues
- repair succeeded when repair was necessary

The goal is not to reward verbose prose. The goal is to measure whether the Agent system completed the planning task under explicit constraints.

## 5. GIS Evaluation

GIS behavior should be evaluated separately from LLM quality.

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

## 6. Repair Evaluation

Repair should answer three questions:

1. How often does the first plan violate deterministic constraints?
2. When repair is triggered, how often are the blocking issues removed?
3. Does repair introduce new problems?

Recommended breakdown:

```text
initial_validation_pass_rate
repair_trigger_rate
repair_success_rate
post_repair_degraded_rate
```

## 7. Revision Lock Evaluation

Lock behavior is covered with deterministic tests because it should not depend on model judgment.

Important cases:

- locked day remains unchanged
- locked hotel remains unchanged
- locked attraction remains unchanged
- unrelated unlocked content can still change
- a mixed sentence such as "第一天不要改，把第三天改轻松一点" does not over-lock the third day
- lock violations are recorded in Trace

## 8. GitHub Actions

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

The workflow performs a secret preflight before any live API call. It requires repository Actions secrets for the model and AMap credentials. If they are unavailable, the live evaluation step is skipped instead of pretending that a real evaluation succeeded.

At the time this document was organized, the Live Agent Eval workflow itself completed successfully, but the real 3-case Agent run was skipped because the required Actions secrets were not available to the workflow.

## 9. Credentials

Example local environment values are documented in `backend/.env.example`.

Typical live-eval requirements include:

```text
LLM_API_KEY
AMAP_API_KEY
```

Do not commit real credentials into the repository.

## 10. How to Use Results in a Resume

Only use metrics after a reproducible real run.

Good examples after measurement:

```text
Structured Output Success Rate: 98%
Constraint Validation Pass Rate: 91%
Repair Success Rate: 87%
Average GIS Travel-Time Reduction: 14.2 min/day
P95 End-to-End Latency: 18.6 s
```

Do not put placeholder or estimated percentages in a resume. Keep the README benchmark section empty until the live environment has produced a report.