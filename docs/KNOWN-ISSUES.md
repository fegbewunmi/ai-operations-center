# Known Issues

Tracked gaps found during development that are real but out of scope for the
change that surfaced them. Each entry has enough detail to pick back up later
without re-deriving the root cause.

---

## 1. L3 approval resume doesn't actually dispatch anything

**RESOLVED 2026-09-01 — see `docs/decisions/ADR-015-l3-approval-and-challenge-resume-fix.md`.**
`dispatcher_node` now routes an L3 decision to a dedicated `l3_approval_gate`
node that pauses via LangGraph's `interrupt()`; `POST /approval` resumes with
`Command(resume=...)`. The same investigation also surfaced that hypothesis
"challenge"-resume had an identical dead end via a different code path -
fixed in the same ADR via `Command(goto="planner", ...)`. Left below for
history.

**Symptom:** Approving an L3-escalated investigation via
`POST /v1/investigations/{id}/approval` never causes the recommended action to
be dispatched. No `DispatchedAction` is created, `incident_memory` is never
written, and no Slack/PagerDuty mock fires - only `state["phase"]` flips to
`"responding"`.

**Root cause:** `dispatcher_node` (`app/graph/nodes/dispatcher.py`) has a
fixed, unconditional `dispatcher -> END` edge in `app/graph/graph.py`, and
nothing in the graph calls `interrupt()`. Once `dispatcher_node` runs and
decides "L3, don't dispatch," the graph reaches a genuine `END` with an empty
checkpoint task queue. `submit_approval`'s resume
(`graph.aupdate_state(values={"phase": "responding"})` followed by
`graph.ainvoke(None, config)`) has nothing left to re-enter - it only rewrites
the `phase` field.

`ADR-004` (`docs/decisions/ADR-004-human-approval-pattern.md`) describes
resuming "from the Response Agent node" - a distinct node the graph would
re-enter post-approval to perform the real dispatch. That node
(`app/graph/nodes/response.py`, `response_node`) still exists in the repo but
is dead code - it's imported nowhere; `dispatcher.py` superseded it without
preserving the re-entry point the ADR assumed.

**Confirmed empirically** 2026-09-01: ran the real `dispatcher_node` through a
minimal graph reproducing the exact `dispatcher -> END` edge, fed it a
synthetic `L3` hypothesis (no LLM calls needed), and drove it through the
identical `aupdate_state` + `ainvoke(None, ...)` sequence `submit_approval`
uses. `dispatched_actions` stayed `[]` across the "resume."

**Fix direction (not yet done):** give the graph a real pause/resume point for
L3 actions - either wire `response_node` back in as a distinct node between
approval and a real dispatch, or use `interrupt()` and resume with
`Command(resume=...)` per LangGraph's native mechanism (traded off against
Option A's Cloud Run cost concerns in ADR-004 - may need a fresh ADR to
resolve).

---

## 2. `pending_approvals` database table is never written to

**RESOLVED 2026-09-01 — see `docs/decisions/ADR-015-l3-approval-and-challenge-resume-fix.md`.**
Resolved alongside #1: `dispatcher_node`/`l3_approval_gate_node` now write
and update real rows, and `GET /{id}/analysis` reads `pending_approvals` from
this table instead of (now-unused) checkpoint state. Left below for history.

**Symptom:** The `pending_approvals` table (`migrations/versions/0001_initial_schema.py`)
has no code path that ever inserts into it.

**Root cause:** `dispatcher_node` builds a `PendingApproval` Pydantic object
and appends it to the LangGraph checkpoint state
(`state["pending_approvals"]`) - it never issues an `INSERT`. The only
durable record of a pending L3 approval is inside the LangGraph checkpoint
blob, not a queryable SQL row. Any tooling or dashboard that expects to list
pending approvals via SQL will find the table permanently empty.

**Fix direction (not yet done):** either have `dispatcher_node` (or a wrapper
in `investigations.py`) write the row when creating a `PendingApproval`, or
drop the table if the checkpoint-only approach is intentional going forward.
Related to #1 - worth resolving together.

---

## 3. Knowledge search `relevance_score` is meaningless

**Symptom:** `search_documents`/`POST /v1/knowledge/search` (and the
`knowledge_node` it's derived from) always return `relevance_score` at or near
`0`, regardless of how relevant the match actually is.

**Root cause:** documents were embedded at seed time
(`backend/scripts/seed_data.py:25`) with `gemini-embedding-001`, but queries
are embedded at search time (`app/graph/nodes/knowledge.py:_embed_query`)
with `settings.embedding_model`, which defaults to `text-embedding-004`
(`app/config.py`, `.env.example`). Two different embedding models produce
vectors in incompatible spaces - cosine distance between them lands near 1.0
(orthogonal) regardless of true semantic similarity.

**Confirmed live** 2026-08-31 via `POST /v1/knowledge/search`: querying
"database timeout" returned the correct top document but with
`relevance_score: 0`.

**Fix direction (not yet done):** align on one embedding model everywhere -
either switch `_embed_query` to `gemini-embedding-001`, or re-seed
`documents` with `text-embedding-004`.

---

## 4. `list_investigations`' `completed_at` is always `null`

**Symptom:** `GET /v1/investigations` reports `completed_at: null` for every
investigation, even ones that finished normally and show `phase: "complete"`.
`GET /v1/investigations/{id}/status` for the *same* investigation correctly
returns a real `completed_at`.

**Root cause:** `list_investigations` (`app/api/v1/investigations.py`) reads
`phase` from the live LangGraph checkpoint but reads `completed_at` straight
from the `investigations` DB row. That column is only ever written by
`_run_investigation`'s exception/escalation handler - never on normal
completion.

**Fix direction (not yet done):** write `completed_at` to the `investigations`
row wherever a normal completion actually happens (e.g. in
`dispatcher_node`'s L1/L2 path, alongside its existing `UPDATE investigations
SET phase = 'complete' ...` in `_write_incident_memory`).
