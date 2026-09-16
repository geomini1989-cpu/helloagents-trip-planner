# Local Knowledge Agent

## Why It Exists

AMap is used for POI, weather, hotel and route data, but a usable trip plan also needs operational facts that often change independently from map data:

```text
opening hours
last-entry time
reservation requirements
fixed closure days
ticket / admission rules
temporary closure notices
holiday-specific announcements
```

The Local Knowledge Agent owns this separate responsibility. It does **not** search for more attractions and it does **not** replace the Attraction Agent.

## Data Boundary

```text
Attraction Agent
→ AMap MCP
→ candidate POIs / addresses / coordinates

Local Knowledge Agent
→ independent Web Search provider
→ source-backed operating rules
```

The default Web Search provider is Tavily. The backend calls the provider directly, then gives the returned source snippets and URLs to a request-scoped Local Knowledge Agent for verification and summarization.

This keeps the source boundary explicit:

```text
Web Search → obtains evidence
Local Knowledge Agent → interprets evidence
Planner / Revision → uses verified rules
```

The model is not allowed to invent opening hours, reservation rules, closure days, ticket rules or temporary notices from memory.

## Full Planning Flow

Local Knowledge depends on the attraction candidates, so it intentionally does not run in the first fan-out group.

```text
Attraction ┐
Weather    ├─ parallel retrieval
Hotel      ┘
    ↓
Attraction result
    ↓
Local Knowledge Agent
    ↓
Planner
    ↓
GIS
    ↓
Validator
```

The Planner receives the Local Knowledge summary in addition to AMap retrieval results. Verified closure/reservation/admission rules should influence the itinerary; unverified information can only be surfaced as a warning.

## Dynamic Session Flow

The Coordinator supports the intent:

```text
poi_rules_check
```

Example:

```text
“故宫需要提前预约吗？如果周一闭馆就调整行程。”
```

Canonical graph:

```text
Coordinator
→ Local Knowledge Agent
→ Revision Agent
→ GIS
→ Validator
```

This avoids rerunning Weather, Hotel or the full Planner when the user only needs an operating-rule check.

## Reliability

`TAVILY_API_KEY` is optional for ordinary local development, but without it Local Knowledge returns an explicit degraded result:

```text
TAVILY_API_KEY not configured
```

The system then tells downstream Agents not to guess missing operating facts.

Trace events record:

```text
knowledge_provider
knowledge_sources[]
source title
source URL
relevance score
fallback / error category
latency
```

The Live Eval workflow requires `TAVILY_API_KEY` together with the LLM and AMap secrets so that the complete Agent pipeline is evaluated rather than silently skipping the new capability.

## Configuration

```bash
TAVILY_API_KEY=your_tavily_api_key_here
LOCAL_KNOWLEDGE_MAX_RESULTS=5
LOCAL_KNOWLEDGE_TIMEOUT_SECONDS=12
```

## Design Principle

This Agent was added because it owns a genuinely different source of information and failure mode. It is not an Agent-count optimization.

The project still keeps deterministic problems outside the Agent layer:

```text
GIS route ordering → deterministic spatial/network optimization
budget / route constraints → deterministic Validator
confirmed-field protection → deterministic Lock Guard
```
