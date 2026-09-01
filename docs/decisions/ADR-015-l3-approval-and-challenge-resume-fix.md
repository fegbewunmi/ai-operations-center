# ADR-015: Fixing L3 Approval and Challenge Resume via interrupt()/Command

**Status:** Accepted
**Date:** 2026-09-01

---

## Context

`docs/KNOWN-ISSUES.md` #1 (found while building the MCP server, `ADR-013`) documented that approving an L3-escalated investigation never actually dispatches anything: `dispatcher_node` had an unconditional `dispatcher -> END` edge, so once it decided "L3, don't dispatch," the graph reached a genuine `END` with an empty pending-task queue. `POST /approval`'s resume (`graph.aupdate_state(phase="responding")` + `graph.ainvoke(None, config)`) had nothing left to continue into.

While fixing this, the same question was asked of `POST /{id}/hypotheses/{id}/feedback`'s "challenged" verdict, which uses the identical pattern (`aupdate_state` + `ainvoke(None, ...)`) and is gated to only fire when `phase` is `"complete"` or `"escalated"` — i.e., only ever called after the graph has already reached a real `END`. `ADR-014` had claimed this path was unaffected, "confirmed by" a re-invocation that, on inspection, was never actually observed. It has the same bug.

**Verification methodology:** rather than reason about LangGraph's checkpoint semantics abstractly, each claim below was checked with a minimal, disposable repro script (a trivial 1-2 node `StateGraph` + `MemorySaver`, a call counter) before any production code was written or any conclusion was documented:

1. `aupdate_state(...)` + `ainvoke(None, config)` on a thread that already reached true `END` — the node's call counter did not increment; state was unchanged. **Confirmed broken**, for both the approval and challenge cases.
2. `ainvoke(Command(resume=value), config)` on a thread paused via `interrupt()` inside a node — resumes execution at that exact point, with `interrupt()` returning `value`. **Confirmed working**, and confirmed the process does not need to stay alive between the pause and the resume (the pause is `GraphInterrupt` unwinding the stack; the checkpointer persists it; a brand-new process can call `ainvoke` later).
3. `ainvoke(Command(goto="node_name"), config)` on an already-finished thread (`next == ()`) — re-enters at `node_name` and runs it once, then proceeds via that node's normal edges. **Confirmed working**, including combined with `update=` in the same `Command`.
4. The two new DB helpers (`_create_pending_approval`, `_resolve_pending_approval`) were run against a real scratch Postgres (migrated via `alembic upgrade head`, never the user's Cloud SQL) before being wired into the graph, given this codebase's history of raw-SQL casting bugs (`:param::type` spacing, string-vs-datetime binding — both found and fixed earlier this project).

---

## Decision 1: L3 approval uses interrupt() + Command(resume=...), via a new l3_approval_gate node

**Why not just call `interrupt()` inside `dispatcher_node` itself:** code before an `interrupt()` call re-executes in full every time the node resumes (LangGraph replays the node function from the top; only the `interrupt()` call site itself short-circuits to the resume value on replay). Putting `interrupt()` directly in `dispatcher_node`, after building and inserting a `PendingApproval`, would require making that insert idempotent against replay. Instead, `dispatcher_node` keeps its existing, non-paused shape - it runs exactly once, durably records the approval request (`_create_pending_approval`), sets `phase="escalated"`, and routes via a new conditional edge (`route_from_dispatcher`) to a dedicated `l3_approval_gate_node` for L3, or straight to `END` for L1/L2 (unchanged). `l3_approval_gate_node`'s entire body is the `interrupt()` call followed by dispatch-or-reject logic - nothing precedes the interrupt, so there is nothing to make idempotent.

This is close to `ADR-004`'s original "Response Agent" idea (a distinct node the graph re-enters post-approval) - `l3_approval_gate_node` is that node - but reached via LangGraph's own interrupt/resume primitive instead of a hand-rolled reach-`END`-and-hope.

**`ADR-004`'s rejection of `interrupt()` was based on an incorrect premise.** It assumed the graph process "must remain running while waiting for human approval." A checkpointed `interrupt()` does not require this: it raises internally, the checkpointer durably persists the pause (`snapshot.next == ("l3_approval_gate",)`), and the process is free to exit - Cloud Run scales to zero exactly as `ADR-004` wanted from terminate-checkpoint-resume. `POST /approval` resumes via a brand-new `graph.ainvoke(Command(resume=...), config)` call, which can happen in an entirely new container/invocation, minutes or days later. This supersedes `ADR-004`'s Option A concern for this codebase's installed LangGraph version (1.2.10); `ADR-004` itself is left as-is for historical context rather than rewritten.

**`submit_approval`'s guard condition changed** from `state["phase"] != "escalated"` to `snapshot.next != ("l3_approval_gate",)`. The old check was actually looser than intended - several other nodes also set `phase="escalated"` for unrelated reasons (budget exhaustion, low-confidence escalation), so the old guard would have let an approval request through against an investigation that was never actually awaiting L3 approval. Checking `snapshot.next` asks LangGraph directly "is this thread paused at the approval gate," which is precise by construction.

**Both approve and reject now resume the graph** (previously, reject only wrote `escalation_reason` and returned, never touching the graph - meaning a rejected L3 investigation was left in `phase="escalated"` forever with no closing event). `l3_approval_gate_node` itself branches on the resumed decision: rejected stays `phase="escalated"` with a timeline event recording who rejected it and why; approved builds the `DispatchedAction`, writes `incident_memory`, and sets `phase="complete"`.

---

## Decision 2: Challenge-resume uses Command(update=..., goto="planner")

Same underlying bug, different shape: challenge doesn't want to resume a *paused* node (nothing paused it - the graph genuinely finished), it wants to *restart* execution at the `planner` node on an already-finished thread. `Command(goto="planner", update={"phase": "planning"})` does exactly this in one call - LangGraph applies the state update, then treats `planner` as the next node to run, exactly as if the graph had routed there normally. `submit_hypothesis_feedback` also now rejects a challenge attempt while `snapshot.next == ("l3_approval_gate",)` (awaiting L3 approval) - re-entering at `planner` while a different task is genuinely paused mid-interrupt is not a state this codebase needs to support, and the two features being triggerable at once was never a designed interaction.

---

## Decision 3: `pending_approvals` DB table becomes the source of truth for "is one pending"

`docs/KNOWN-ISSUES.md` #2 (the `pending_approvals` table is never written to) is fixed in the same pass, since it's directly entangled with Decision 1: while `l3_approval_gate_node` is paused inside `interrupt()`, it has not returned, so nothing it *would* return (a `PendingApproval` appended to checkpoint state) actually exists in the checkpoint yet - `state["pending_approvals"]` would stay empty for the entire duration of the pause if it depended on the node's return value. The graph nodes therefore stopped writing to `state["pending_approvals"]` entirely (the field stays in `InvestigationState` for schema compatibility but is always `[]` going forward) and `GET /{id}/analysis` now reads pending/resolved approvals directly from the `pending_approvals` SQL table instead, which `dispatcher_node`/`l3_approval_gate_node` write to as a real, immediately-committed side effect independent of the checkpoint.

**Options considered for the checkpoint-state list:**
- **Keep appending to `state["pending_approvals"]` alongside the DB write** - rejected: since `dispatcher_node` and `l3_approval_gate_node` each only run once and can't share a single list entry through a pause, this would append two separate `PendingApproval` objects (one at creation, one at resolution) to an `operator.add`-reduced list, and the frontend's `pending_approvals.some(p => p.approved === null)` check would then never clear once resolved, since the original unresolved entry would still be present.
- **Give `pending_approvals` a different reducer that replaces by `approval_id` instead of appending** - would work, but changes the semantics of every other list field's shared `operator.add` convention for one field, for no benefit once the SQL table already exists as a proper queryable, updatable record.
- **SQL table as source of truth (selected)** - a table already exists for exactly this purpose (`migrations/0001`); using it doesn't require reasoning about interrupt/replay semantics for the historical record at all, and it's what "the DB table is never written to" was asking for regardless of the L3 fix.

---

## Consequences

- `DispatchedAction.authority_level` widened from `Literal["L1", "L2"]` to `Literal["L1", "L2", "L3"]` (`app/shared/schemas/response.py`, `frontend/lib/types.ts`) - an approved L3 action can now actually produce a `DispatchedAction` for the first time in this codebase's history.
- `docs/KNOWN-ISSUES.md` #1 and #2 are resolved; #3 (knowledge search `relevance_score`) and #4 (`list_investigations.completed_at`) remain open and unrelated to this change.
- New graph node `l3_approval_gate` appears in `snapshot.next`/Cloud Trace for any L3-escalated investigation; `AgentActivityPanel`'s node-name mapping doesn't need updating since it only renders `TimelineEvent.source`/`NodeTokenUsage.node`, neither of which this node emits differently from `dispatcher`'s existing `"action_dispatcher"` source string.
- `tests/test_dispatcher_node.py` (unit tests for both nodes, a real-compiled-subgraph test proving the interrupt/resume mechanics against production code, and one `@integration` test against real Postgres) and `tests/test_approval_and_challenge_api.py` (endpoint-level tests with a mocked graph, proving the endpoints construct the right `Command` given a snapshot) were added; both need `DATABASE_URL`/`GCP_PROJECT_ID` set to import (same pre-existing constraint as `test_deployment_node.py`/`test_tickets_api.py`), so they're grouped with those in the README's integration-test command rather than the DB-free one.
