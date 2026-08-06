# ADR-010: Planner Routing Bug — Diagnosis and Fix

**Status:** Resolved  
**Date:** August 6, 2026  
**Context:** First end-to-end eval harness run

---

## Background

The evaluation harness was designed to test agent reasoning against fixture data, with all external APIs mocked and all LLM calls real. The first real eval run surfaced a cascade of three bugs that prevented the investigation graph from completing. This ADR records the symptoms, diagnostic process, root causes, and fixes — mirroring a production incident postmortem.

---

## Symptom

All three eval fixtures produced the same output: 7 iterations, only the telemetry agent called, budget exhausted, no synthesis, no output. The scorer reported `called=['telemetry']` regardless of which fixture was run.

```
Incident: INC-FD-001
Phase:    investigating
Specialists: FAIL  called=['telemetry'], required=['deployment', 'telemetry']
Iterations: 7  Tool calls: 7
ERRORS: No synthesis output
```

---

## Bug 1: Undiscriminated AgentQuery Union

### Root cause

`AgentQuery` was defined as an undiscriminated Pydantic union:

```python
AgentQuery = Annotated[
    Union[TelemetryQuery, DeploymentQuery, KnowledgeQuery],
    "Query passed from Planner to the target specialist agent"
]
```

When `llm.with_structured_output(PlannerDecision)` generates a JSON schema for Gemini, an undiscriminated union produces an `anyOf` with no discriminator property. Gemini defaults to satisfying the first schema (`TelemetryQuery`) because it is the safest choice when no signal distinguishes the variants. `TelemetryQuery` also requires a `focus` field the others don't — so Gemini always included `focus`, causing Pydantic's left-to-right union parsing to always match `TelemetryQuery`.

A second contributing issue: the planner system prompt used soft RULE lines ("Do NOT invoke an agent already called"). In structured output mode, natural language instructions compete with schema compliance. The model's primary objective is to produce valid JSON; prose rules have lower effective priority.

### Fix

Added a `query_type: Literal[...]` discriminator field with a default to each query type:

```python
class TelemetryQuery(BaseModel):
    query_type: Literal["telemetry"] = "telemetry"
    ...

class DeploymentQuery(BaseModel):
    query_type: Literal["deployment"] = "deployment"
    ...

class KnowledgeQuery(BaseModel):
    query_type: Literal["knowledge"] = "knowledge"
    ...

AgentQuery = Annotated[
    Union[TelemetryQuery, DeploymentQuery, KnowledgeQuery],
    Field(discriminator="query_type")
]
```

The planner system prompt was updated to explicitly require `query.query_type` to match `agent`. The user message was updated to replace soft RULE lines with an explicit FORBIDDEN ACTIONS block listing already-called agents, and a REQUIRED NEXT ACTION directive.

### Outcome

After this fix, the planner correctly routed: `agent=telemetry` on iteration 0, `agent=deployment` on iteration 1, `agent=knowledge` on iteration 2, `action=synthesize` on iteration 3. But the scorer still showed `called=['telemetry']` — a second bug was masking the successful routing.

---

## Bug 2: Eval Mock Parameter Name Mismatch

### Root cause

The deployment mock in `eval/runner.py` defined a parameter named `onset_dt_arg`:

```python
async def _mock(service_name, window_start, window_end, onset_dt_arg: datetime):
```

But the deployment node calls `fetch_deployments` with a keyword argument:

```python
records = await fetch_deployments(
    service_name=query.service_name,
    window_start=query.time_window.start,
    window_end=query.time_window.end,
    onset_dt=onset_dt,          # keyword: "onset_dt"
)
```

Python raised `TypeError: _mock() got an unexpected keyword argument 'onset_dt'`. The deployment node's `except Exception` block caught it silently and returned an error log entry with no `deployment_findings` and no timeline event. The scorer never saw a `deployment_agent` event; the planner never received new evidence. It kept choosing deployment on every iteration, hitting the same crash each time — explaining why its reason text barely changed across iterations 2–6.

### Diagnostic tell

The planner reason text was nearly identical on iterations 2–6. A working deployment call would give the planner new information, changing its next reasoning step. The static reason text indicated the planner was not seeing new evidence.

### Fix

```python
# runner.py
async def _mock(service_name, window_start, window_end, onset_dt: datetime):
```

### Lesson

Silent exception handling in nodes is correct production behavior — a failing tool should not crash the graph. But it creates a diagnostic blind spot in eval: test bugs look identical to production failures at the eval layer. Check `error_log` in state when an agent appears to produce no output.

---

## Bug 3: Missing `action_dispatched` in TimelineEvent Schema

### Root cause

With the prior two bugs fixed, the graph completed a full investigation run for the first time and reached the dispatcher. The dispatcher creates a `TimelineEvent` with `event_type='action_dispatched'` to record the dispatched action. That value was not in the `Literal` for `TimelineEvent.event_type` in `app/shared/schemas/core.py`:

```
ValidationError: 1 validation error for TimelineEvent
event_type
  Input should be 'alert', 'deployment', ... 'approval_granted'
  [input_value='action_dispatched']
```

The dispatcher had been written but never reached in any prior run — this was its first real execution.

### Fix

```python
event_type: Literal[
    "alert", "deployment", "metric_anomaly", "log_event",
    "config_change", "investigation_finding", "synthesis",
    "approval_requested", "approval_granted",
    "action_dispatched",   # added
]
```

---

## Final Result (INC-FD-001)

```
Phase:       complete
Accuracy:    PASS  (category=True, service=True)
Evidence:    PASS  (>=2 supporting items)
Specialists: PASS  called=['deployment', 'knowledge', 'telemetry']
Confidence:  85%
MTTFH:       48s
Iterations:  4  Tool calls: 4

✓ Root-cause accuracy >= 75%:   100%
✓ Evidence completeness >= 80%: 100%
✓ Required specialists >= 90%:  100%
✓ Avg MTTFH < 5 min:            48s
```

---

## Files Changed

| File | Change |
|---|---|
| `app/shared/schemas/planner.py` | Added `query_type` discriminator to all three query types; `AgentQuery` uses `Field(discriminator="query_type")` |
| `app/graph/nodes/planner.py` | FORBIDDEN ACTIONS + REQUIRED NEXT ACTION blocks in user message; `query_type` alignment rule in system prompt |
| `app/shared/schemas/core.py` | Added `"action_dispatched"` to `TimelineEvent.event_type` |
| `eval/runner.py` | Fixed mock parameter name: `onset_dt_arg` → `onset_dt` |

---

## Key Takeaways

**Schema design affects LLM behavior.** An undiscriminated `anyOf` union in a structured output schema can cause an LLM to always choose the first variant. Discriminated unions with `const`-valued discriminator fields make the intended type unambiguous.

**Eval harness bugs hide system bugs.** Bug 2 masked Bug 1's fix for an entire eval round. When an agent produces no output and no error is surfaced, check the node's error_log return path.

**Silent exception handling has a cost.** Correct for production resilience; opaque for debugging. Add observability (error_log, timeline events) at every failure path.

**The path to a passing eval is rarely straight.** Three independent bugs — schema, mock, schema — each surfaced only when the previous one was fixed. Systematic debugging (one hypothesis at a time, debug instrumentation at the decision boundary) was more efficient than guessing.
