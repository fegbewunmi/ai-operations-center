# ADR-004: Level 3 Actions Use Terminate-Checkpoint-Resume

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

The system has three authority levels. Level 3 actions (rollback, service restart, scale, disable deployment) require human approval before execution. The system must pause execution, present the proposed action to an operator, and resume upon approval — potentially minutes or hours later.

Two mechanisms are available in LangGraph for implementing this pause.

---

## Options considered

**Option A: LangGraph interrupt() — suspend in place**
- Pros: The graph state is held in memory; resumption is simple (call `Command(resume=...)`)
- Cons: The graph execution process must remain running while waiting for human approval. On Cloud Run, a container that is running but waiting for input consumes resources and counts against concurrency limits. Cloud Run is designed to scale to zero between requests — a waiting process works against this. If the container is restarted (Cloud Run can reclaim instances), the waiting state is lost. This pattern is fragile on a serverless platform.

**Option B: Terminate + checkpoint + resume via new invocation (selected)**
- Pros: The graph terminates after writing the approval request to Cloud SQL and a LangGraph checkpoint to the database. Cloud Run scales to zero. When a human approves via the FastAPI endpoint, a new Cloud Run invocation loads the checkpoint and resumes from the Response Agent node. This is stateless from the platform's perspective, which is exactly what Cloud Run is designed for.
- Cons: More complex implementation — requires a FastAPI approval endpoint, a `pending_approvals` table, and logic to resume from a checkpoint. Resumption creates a new LangGraph execution context, which must be tested carefully.

---

## Decision

Level 3 actions use terminate-checkpoint-resume.

**Flow:**
1. Response Agent identifies a Level 3 action in the Synthesizer output
2. Writes an approval record to Cloud SQL: `{approval_id, incident_id, action_description, checkpoint_id, requested_at, approved: null}`
3. LangGraph checkpoints full graph state to Postgres at this point
4. Graph terminates; Cloud Run instance is released
5. Human receives notification (Slack/PagerDuty) with approval link
6. Human approves via `POST /incidents/{id}/approvals/{approval_id}`
7. FastAPI handler loads the checkpoint, resumes graph execution from the Response Agent node with `approved: true`

**Authority level determination:** The Safety Guard sets `risk_level` on `ValidationResult`. The Response Agent reads this to decide whether the action is L1/L2 (proceed) or L3 (checkpoint and terminate).

---

## Consequences

- Cloud Run can scale to zero between investigation and approval — no idle resource cost
- Approval records and checkpoint IDs must be stored durably in Cloud SQL (not in-memory) to survive container restarts
- LangGraph's Postgres checkpointer must be configured from the start (not an afterthought)
- The FastAPI approval endpoint is a security-sensitive surface — it requires authentication (not anonymous)
- Resumption from checkpoint must be tested as a distinct code path; it has different failure modes than normal invocation
