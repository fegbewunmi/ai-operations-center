# System Architecture

*Status: Section 3 locked. Last updated: 2026-08-06.*

---

## Orchestration pattern

**Decision:** Iterative Planner loop implemented with LangGraph.

Rationale and alternatives considered: [ADR-002](decisions/ADR-002-planner-iterative-loop.md).

The Planner is the single control node. Every specialist (Telemetry, Deployment, Knowledge) returns results to the Planner, which decides the next step. The Synthesizer is called exactly once, when the Planner determines the evidence is sufficient or the investigation budget is exhausted.

---

## Graph topology

```
START
  │
  ▼
Planner ◄──────────────────────────────────────────────┐
  │                                                    │
  │ (conditional on planner_decision.action)           │
  ├── "invoke" + agent="telemetry"  → Telemetry ───────┤
  ├── "invoke" + agent="deployment" → Deployment ──────┤
  ├── "invoke" + agent="knowledge"  → Knowledge ───────┘
  │
  ├── "synthesize" → Synthesizer
  │                      │
  │                  Safety Guard
  │                      │
  │           (conditional on validation_result.passed)
  │                ├── True  → Response Agent → END
  │                ├── False + budget remaining → Planner (re-investigate)
  │                └── False + budget exhausted → END (escalated)
  │
  └── "escalate" → END
```

**Key routing mechanics in LangGraph:**

A conditional edge routing function returns a string (the next node name) — it cannot pass parameters. The Planner communicates its decision to the next node by writing a `planner_decision` field to shared state before the edge fires. Specialists read their typed query from `state["planner_decision"].query`. This is why `planner_decision` is a first-class field in `InvestigationState`.

After any specialist runs, it returns to the Planner via an unconditional edge. The Planner then sees the updated state (including the new findings) and makes its next decision.

---

## Graph definition (structure — not agent logic)

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver

def build_investigation_graph(checkpointer: PostgresSaver) -> CompiledGraph:
    graph = StateGraph(InvestigationState)

    graph.add_node("planner", planner_node)
    graph.add_node("telemetry", telemetry_node)
    graph.add_node("deployment", deployment_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("safety_guard", safety_guard_node)
    graph.add_node("response", response_node)

    graph.add_edge(START, "planner")

    graph.add_conditional_edges(
        "planner",
        route_from_planner,   # reads state["planner_decision"].action
        {
            "telemetry":  "telemetry",
            "deployment": "deployment",
            "knowledge":  "knowledge",
            "synthesize": "synthesizer",
            "escalate":   END,
        }
    )

    # Specialists always return to Planner
    graph.add_edge("telemetry",  "planner")
    graph.add_edge("deployment", "planner")
    graph.add_edge("knowledge",  "planner")

    graph.add_edge("synthesizer", "safety_guard")

    graph.add_conditional_edges(
        "safety_guard",
        route_from_safety_guard,  # reads state["validation_result"]
        {
            "respond":       "response",
            "reinvestigate": "planner",
            "escalate":      END,
        }
    )

    graph.add_edge("response", END)

    return graph.compile(checkpointer=checkpointer)
```

**Routing functions:**

```python
def route_from_planner(state: InvestigationState) -> str:
    decision = state["planner_decision"]
    if decision.action == "invoke":
        return decision.agent  # "telemetry" | "deployment" | "knowledge"
    return decision.action     # "synthesize" | "escalate"

def route_from_safety_guard(state: InvestigationState) -> str:
    result = state["validation_result"]
    if result.passed:
        return "respond"
    budget = state["budget"]
    if budget.iterations_remaining > 0:
        return "reinvestigate"
    return "escalate"
```

---

## Shared state: InvestigationState

Every node reads from and writes to this TypedDict. LangGraph merges node return values into state — nodes only return the fields they update.

Fields annotated with `operator.add` use list concatenation as the merge operation (append, not overwrite). This allows the same specialist to be called multiple times without losing earlier findings.

```python
import operator
from typing import Annotated, TypedDict, Literal
from datetime import datetime

class InvestigationState(TypedDict):
    # ── Identity ─────────────────────────────────────────────────────────────
    investigation_id: str           # also used as LangGraph thread_id
    incident: IncidentTrigger

    # ── Control flow ─────────────────────────────────────────────────────────
    phase: Literal[
        "planning", "investigating", "synthesizing",
        "responding", "complete", "escalated"
    ]
    planner_decision: PlannerDecision | None   # written by Planner, read by specialists
    planner_working_hypothesis: str | None     # informal; drives loop termination
    planner_working_confidence: float          # 0.0–1.0; compared to budget.confidence_threshold
    budget: InvestigationBudget

    # ── Accumulated evidence ──────────────────────────────────────────────────
    # Annotated with operator.add so each specialist call appends, not overwrites
    timeline: Annotated[list[TimelineEvent], operator.add]
    telemetry_findings: Annotated[list[TelemetryFindings], operator.add]
    deployment_findings: Annotated[list[DeploymentFindings], operator.add]
    knowledge_context: Annotated[list[KnowledgeContext], operator.add]
    service_topology: ServiceTopology | None   # fetched once by Planner on first iteration

    # ── Synthesis outputs ─────────────────────────────────────────────────────
    synthesis: SynthesisOutput | None
    validation_result: ValidationResult | None

    # ── Response outputs ──────────────────────────────────────────────────────
    dispatched_actions: Annotated[list[DispatchedAction], operator.add]
    pending_approvals: Annotated[list[PendingApproval], operator.add]

    # ── Metadata ─────────────────────────────────────────────────────────────
    started_at: datetime
    completed_at: datetime | None
    escalation_reason: str | None
    error_log: Annotated[list[AgentError], operator.add]
```

**State update example — Telemetry Agent returning findings:**

```python
# Telemetry node returns only the fields it touches.
# LangGraph merges this into the existing state.
def telemetry_node(state: InvestigationState) -> dict:
    query = state["planner_decision"].query  # TelemetryQuery typed
    findings = run_telemetry_tools(query)
    event = TimelineEvent(
        timestamp=datetime.utcnow(),
        event_type="investigation_finding",
        service=query.service_name,
        description=f"Telemetry gathered: {findings.summary}",
        source="telemetry_agent",
    )
    return {
        "telemetry_findings": [findings],  # appended via operator.add
        "timeline": [event],               # appended via operator.add
    }
```

---

## Planner decision schema

```python
from typing import Union

class TelemetryQuery(BaseModel):
    service_name: str
    time_window: TimeWindow
    investigation_query: str
    focus: list[Literal["metrics", "logs", "traces"]]

class DeploymentQuery(BaseModel):
    service_name: str
    time_window: TimeWindow
    investigation_query: str

class KnowledgeQuery(BaseModel):
    query: str
    service_name: str | None = None
    document_types: list[Literal["runbook", "postmortem", "architecture_doc", "error_pattern"]] | None = None

AgentQuery = Union[TelemetryQuery, DeploymentQuery, KnowledgeQuery]

class PlannerDecision(BaseModel):
    action: Literal["invoke", "synthesize", "escalate"]
    agent: Literal["telemetry", "deployment", "knowledge"] | None = None
    query: AgentQuery | None = None
    reason: str   # why the Planner made this decision — logged for eval and debugging
```

The `reason` field is not cosmetic. It is the primary debugging artifact when the Planner makes a bad decision (wrong specialist, redundant query, premature synthesis). The eval harness should record and analyze Planner reasoning logs.

---

## Checkpointing strategy

LangGraph checkpoints the full `InvestigationState` after every node execution automatically, given a configured checkpointer.

**Checkpointer:** `langgraph.checkpoint.postgres.PostgresSaver` — checkpoints stored in Cloud SQL.

**Thread ID = investigation_id:** Every investigation has a unique `investigation_id` (UUID). This is passed as `config={"configurable": {"thread_id": investigation_id}}` when invoking the graph. All checkpoints for that investigation are scoped to that thread.

**Why this matters for the approval flow:** When the Response Agent encounters a Level 3 action, it writes the current `checkpoint_id` to the `pending_approvals` table and the graph terminates. The FastAPI approval endpoint retrieves the checkpoint ID and resumes:

```python
# Starting a new investigation
graph.invoke(
    {"incident": trigger, "investigation_id": investigation_id, ...},
    config={"configurable": {"thread_id": investigation_id}}
)

# Resuming from approval (new Cloud Run invocation)
graph.invoke(
    Command(resume={"approved": True, "approved_by": operator_id}),
    config={"configurable": {"thread_id": investigation_id}}
)
```

**Checkpoint retention:** Checkpoints are retained for the duration of the investigation plus 30 days (configurable). They are the audit trail — every state transition is recoverable. The `error_log` field in state captures agent errors; the checkpoint history captures everything else.

---

## Failure handling

### Transient failures (network timeout, API error)

Each specialist node wraps its tool calls in retry logic with exponential backoff (3 attempts, max 10s delay). If all retries fail, the node returns a degraded result:

```python
class AgentError(BaseModel):
    agent: str
    query: str
    error_type: str
    message: str
    timestamp: datetime
    retries_attempted: int

# Specialist returns degraded findings + error record
return {
    "telemetry_findings": [TelemetryFindings(
        service=query.service_name,
        error="tool_failure",
        summary="Telemetry unavailable after 3 retries",
        # all metric/log fields are empty
    )],
    "error_log": [AgentError(agent="telemetry", ...)],
}
```

The Planner sees the degraded result and reasons about it: can it proceed with other specialists, or is the missing data critical enough to escalate?

**Judgment call embedded here:** I've designed the Planner to receive degraded results and decide — rather than having the specialist fail loudly and halt the graph. This means the Planner's prompt must handle the case where `telemetry_findings[-1].error == "tool_failure"` and reason about what to do. This is the right call for production resilience but adds complexity to Planner prompt engineering.

### LLM refusal or malformed output

If a specialist's LLM call returns a refusal or output that fails Pydantic validation, the node catches the error and returns a degraded result with `confidence: 0.0`. This is treated the same as a tool failure.

### Budget exhaustion

The Planner checks `budget.iterations_remaining` and `budget.tool_calls_remaining` at the start of every iteration before deciding the next action. If exhausted:
- Routes to `synthesize` with the evidence collected so far
- Synthesizer sets `investigation_incomplete: True` on `SynthesisOutput`
- Safety Guard is aware of incomplete investigations when evaluating confidence calibration

### Unrecoverable state

If the Planner itself fails (LLM error, state corruption), the exception propagates and LangGraph marks the thread as failed. The last successful checkpoint is preserved. Recovery requires manual intervention — the FastAPI layer returns a 500 with the `investigation_id` so the operator knows which thread failed. **This is not automatically retried** — a failed Planner iteration may have consumed budget; retrying blindly could loop on the same failure.

---

## Investigation isolation

Each investigation is an independent LangGraph thread (`thread_id = investigation_id`). LangGraph's Postgres checkpointer scopes all state reads and writes to the thread.

There is no global mutable state in the agent code. Service Catalog queries are read-only SQL. Cloud Monitoring and Logging queries are read-only API calls scoped to the service and time window in the query.

Concurrent investigations do not interact. On Cloud Run, each request is independently invoked and may run in the same container instance or a new one — this is irrelevant because state is always loaded from the Postgres checkpointer at invocation start.

---

## FastAPI API surface

The FastAPI layer has two responsibilities: receiving incident triggers and handling human approvals.

```
POST /investigations
    Body: IncidentTrigger
    Response: 202 Accepted, {investigation_id: str}
    Behavior: Validates trigger, creates investigation record in Cloud SQL,
              starts LangGraph graph in background task.

GET  /investigations/{investigation_id}
    Response: InvestigationStatus (phase, budget_consumed, started_at, elapsed_seconds)
    Behavior: Reads current phase from Cloud SQL. Does not load LangGraph state
              (too expensive for polling).

GET  /investigations/{investigation_id}/findings
    Response: SynthesisOutput (if phase == "complete" or "escalated")
    Behavior: Reads synthesis from Cloud SQL (written by graph on completion).
              Returns 404 if investigation not yet complete.

GET  /investigations/{investigation_id}/timeline
    Response: list[TimelineEvent]
    Behavior: Reads timeline from current LangGraph checkpoint. Usable during
              investigation for live progress visibility.

POST /investigations/{investigation_id}/approvals/{approval_id}
    Body: {approved: bool, approved_by: str, notes: str | None}
    Response: 200, {status: "resumed" | "rejected"}
    Behavior: Updates approval record in Cloud SQL, then resumes LangGraph graph
              from stored checkpoint if approved. Requires authentication.
              If rejected, marks investigation as escalated.
```

**Investigation status lifecycle:**

```
created → planning → investigating → synthesizing → responding → complete
                                                              ↘ escalated
                                              (pending_approval) ↗ (on approval)
```

Status is written to Cloud SQL by each major graph transition (not read from the checkpointer), so `GET /investigations/{id}` is a cheap database read rather than a checkpoint load.

---

## Sequence: happy path (failed deployment)

```
1. Alert fires → POST /investigations with IncidentTrigger
2. Graph starts; Planner fetches service topology (Service Catalog tool)
3. Planner: "Deployment near onset? Check Deployment Agent first."
4. Deployment Agent: finds Payments deployment 3 minutes before error spike
5. Planner: "High suspicion. Confirm with Telemetry."
6. Telemetry Agent: error rate spike at deployment timestamp, new stack trace
7. Planner: "Evidence sufficient. working_confidence=0.95 ≥ threshold."
8. Synthesizer: produces hypothesis (DB: Payments deployment, confidence 94%)
9. Safety Guard: all claims grounded, authority level L2 (rollback recommendation)
10. Response Agent: creates Jira ticket, drafts PagerDuty update, tags Payments team
11. Graph completes. Investigation: 4 iterations, ~18 tool calls, ~8 minutes wall clock.
```

## Sequence: Level 3 approval path

```
1–9. Same as above, but top_hypothesis.authority_level = "L3" (rollback + restart)
10. Response Agent: writes PendingApproval to Cloud SQL with checkpoint_id
    Graph terminates. Cloud Run instance released.
11. PagerDuty/Slack notification sent to Payments team on-call.
12. On-call engineer reviews SynthesisOutput at GET /investigations/{id}/findings
13. POST /investigations/{id}/approvals/{approval_id} {approved: true}
14. FastAPI resumes graph from checkpoint.
15. Response Agent executes rollback command (Phase 3 — proposed only in Phase 1).
16. Graph completes.
```
