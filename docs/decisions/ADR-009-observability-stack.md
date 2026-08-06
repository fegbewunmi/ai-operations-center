# ADR-009: GCP-Native Observability with OpenTelemetry

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

The system runs LangGraph agents on Cloud Run, makes LLM calls via Vertex AI, reads from Cloud SQL, and executes multi-step investigations with a variable number of agent invocations per run. Meaningful observability requires three distinct layers:

1. **Infrastructure** — Is Cloud Run healthy? Is Cloud SQL reachable? Request rate, latency, error rate.
2. **Investigation execution** — Which agents ran? How many Planner iterations? What was MTTFH? Did the Safety Guard trigger?
3. **LLM calls** — Per-model-call latency, token usage broken down by agent, estimated cost per investigation.

All three layers require instrumentation. The choice of platform determines how that instrumentation is collected, stored, and visualized.

---

## Options considered

**Option A: Google Cloud Monitoring + Cloud Logging + Cloud Trace (selected)**
- Infrastructure metrics from Cloud Run are emitted automatically with no configuration
- Distributed traces (LangGraph node execution, LLM calls) emitted via OpenTelemetry SDK and collected by Cloud Trace
- Custom investigation metrics pushed to Cloud Monitoring via the Cloud Monitoring API
- Logs structured as JSON to Cloud Logging (Cloud Run captures stdout automatically)
- Zero additional managed services; no additional accounts; consistent with GCP-native project architecture
- LLM observability requires custom OpenTelemetry spans — this is the known tradeoff

**Option B: Prometheus + Grafana**
- Prometheus requires persistent storage for its TSDB — problematic on stateless Cloud Run without additional infrastructure (separate VM or Cloud Run service with Cloud Storage backing)
- Would need Jaeger or Zipkin separately for distributed tracing
- Infrastructure overhead is disproportionate to what the project needs; no tracing story without additional setup
- Rejected: setup cost exceeds benefit; undercuts the GCP-native narrative

**Option C: Datadog**
- Datadog LLM Observability provides built-in model call tracking (token usage, latency, cost) without custom instrumentation
- GCP auto-discovery covers Cloud Run, Cloud SQL, and key managed services
- Rejected: introduces an external managed dependency inconsistent with the GCP-native architecture; Datadog LLM Observability is a genuine strength but the same result is achievable with OpenTelemetry GenAI semantic conventions and custom metrics; an interviewer asking about GCP observability expects a Cloud Monitoring answer, not "I signed up for Datadog"
- Documented as an alternative: teams running Datadog already could swap Cloud Trace for Datadog APM with minimal code changes, since all instrumentation uses the OpenTelemetry API

---

## Decision

Use Google Cloud Monitoring, Cloud Logging, and Cloud Trace as the primary observability stack. All instrumentation uses the OpenTelemetry SDK (vendor-neutral API), with the GCP OpenTelemetry exporters sending data to Cloud Trace and Cloud Monitoring.

---

## Instrumentation design

### Cloud Trace: execution traces

Every investigation is a single trace, scoped by `investigation_id`. Each LangGraph node is a span. Each LLM call within a node is a child span using the OpenTelemetry GenAI semantic conventions.

```
Trace: investigation/{investigation_id}
  Span: planner (iteration 1)
    Span: service_catalog.get_topology
    Span: llm.generate (gemini-2.0-flash)
      Attribute: gen_ai.usage.input_tokens
      Attribute: gen_ai.usage.output_tokens
      Attribute: gen_ai.request.model
  Span: telemetry_agent
    Span: tool.query_monitoring
    Span: tool.query_logging
    Span: llm.generate (gemini-2.0-flash)
  Span: planner (iteration 2)
    Span: llm.generate
  Span: synthesizer
    Span: llm.generate
  Span: safety_guard
  Span: response_agent
```

This gives a flame graph per investigation showing exactly where time was spent, which agents were called, and how many LLM tokens each consumed.

### Cloud Monitoring: custom metrics

All custom metrics use the `custom.googleapis.com/ai_ops/` namespace.

**Investigation metrics** (emitted at investigation completion):

| Metric | Type | Description |
|---|---|---|
| `ai_ops/investigation/mttfh_seconds` | Distribution | Time from trigger to first Synthesizer output |
| `ai_ops/investigation/total_latency_seconds` | Distribution | Time from trigger to graph completion |
| `ai_ops/investigation/planner_iterations` | Distribution | Number of Planner loop iterations |
| `ai_ops/investigation/tool_calls_total` | Distribution | Total specialist agent invocations |
| `ai_ops/investigation/tokens_total` | Distribution | Total tokens (input + output) across all LLM calls |
| `ai_ops/investigation/estimated_cost_usd` | Distribution | Estimated USD cost based on token usage and model pricing |
| `ai_ops/investigation/escalated` | Counter | Count of escalated investigations |
| `ai_ops/investigation/complete` | Counter | Count of successfully completed investigations |

**Quality metrics** (emitted at investigation completion):

| Metric | Type | Description |
|---|---|---|
| `ai_ops/quality/safety_guard_triggered` | Counter | Investigations where Safety Guard failed validation |
| `ai_ops/quality/safety_guard_passed` | Counter | Investigations where Safety Guard passed |
| `ai_ops/quality/evidence_items` | Distribution | Supporting evidence items in top hypothesis |
| `ai_ops/quality/top_hypothesis_confidence` | Distribution | Confidence score of top hypothesis |

**Eval harness metrics** (emitted after each eval run):

| Metric | Type | Description |
|---|---|---|
| `ai_ops/eval/root_cause_accuracy` | Gauge | Fraction of incidents with correct full-match root cause |
| `ai_ops/eval/evidence_completeness_rate` | Gauge | Fraction of incidents with ≥ 2 supporting evidence items |
| `ai_ops/eval/unsupported_claim_rate` | Gauge | Fraction of evidence claims not traceable to findings |
| `ai_ops/eval/incidents_run` | Gauge | Number of incidents in the eval run |

All metrics carry the label `incident_family` where applicable (`failed_deployment`, `latency_regression`, `resource_leak`) so metrics can be filtered and compared by family in dashboards.

### Cloud Logging: structured logs

Every agent node emits structured JSON logs to stdout. Cloud Run captures stdout and forwards to Cloud Logging automatically.

Minimum log fields per agent invocation:

```json
{
  "investigation_id": "uuid",
  "agent": "telemetry_agent",
  "iteration": 2,
  "severity": "INFO",
  "message": "Telemetry findings: error_rate_change_pct=+340%, DB latency p99 +180%",
  "service": "payments",
  "tokens_used": 1240,
  "duration_ms": 2340
}
```

Errors include the full `AgentError` schema as a structured field. The `investigation_id` label on every log entry means Cloud Logging can reconstruct a complete per-investigation log view with a single filter.

---

## Dashboard design

One Cloud Monitoring dashboard with four sections:

**Section 1: Infrastructure health**
- Cloud Run request rate, error rate, latency p50/p99
- Cloud Run instance count (detect scaling events)
- Cloud SQL connection count, query latency
- Cloud Run container CPU and memory

**Section 2: Investigation execution**
- Investigations started / completed / escalated (time series)
- MTTFH distribution (histogram)
- Total latency distribution (histogram)
- Planner iterations distribution
- Safety Guard trigger rate (line chart — should trend downward as prompts improve)

**Section 3: LLM usage and cost**
- Tokens per investigation (distribution, by incident family)
- Estimated cost per investigation (line chart over time)
- Model call latency p50/p99 (from Cloud Trace data)
- Token usage by agent (stacked bar: Planner, Telemetry, Deployment, Knowledge, Synthesizer)

**Section 4: Evaluation results**
- Root-cause accuracy over eval runs (line chart — catches prompt regressions)
- Evidence completeness rate
- Unsupported claim rate
- These are pushed from the eval harness after each run — production and eval share the same metrics backend

---

## Known tradeoff

LLM-specific observability (token usage per call, model latency, prompt logging) requires custom OpenTelemetry instrumentation. The OpenTelemetry GenAI semantic conventions (`gen_ai.*` attributes) cover the standard fields; cost estimation requires mapping token counts to model pricing, which must be updated when Vertex AI pricing changes.

Datadog LLM Observability handles this without custom instrumentation. Teams who already run Datadog could adopt it by swapping the OpenTelemetry exporter — the instrumentation code itself would not change.

---

## Consequences

- All observability data lives in GCP: no additional accounts, no additional billing relationships
- The OpenTelemetry API is vendor-neutral — the exporter can be swapped (e.g., to Datadog) without changing instrumentation code
- Cost estimation requires a maintained pricing map; this is a small maintenance burden
- Cloud Trace's flame graph view gives a direct visual of each investigation's execution path — this is the primary debugging tool for Planner reasoning issues
- The eval harness pushing metrics to Cloud Monitoring means production monitoring and eval quality tracking share a single dashboard — an on-call engineer sees the same accuracy metrics the engineering team sees in eval
