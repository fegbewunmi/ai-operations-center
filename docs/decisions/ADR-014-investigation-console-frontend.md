# ADR-014: Investigation Console Frontend — Architecture Decisions

**Status:** Accepted
**Date:** 2026-09-01

---

## Context

The backend (`backend/app`) had a fully working FastAPI + LangGraph investigation system with no consumer beyond `curl`/`/docs`. The goal was a real investigation console — Incident Library, Investigation Workspace, Evidence Explorer, Evaluation screen — backed by the real graph and its real schemas, not a chat surface and not a static prototype. Several decisions came up that aren't obvious from reading the resulting code, so they're recorded here rather than only in `frontend/`'s commit history.

---

## Decision 1: The central graph models the investigation, not the LangGraph pipeline

**Options considered:**

- **Render the fixed 8-node pipeline** (planner → telemetry/deployment/knowledge → incident_analysis → synthesizer → safety_guard → dispatcher) on the flagship React Flow canvas. Simple to derive — it's a static topology — but it answers "how does Trace work" once and then never changes for the rest of the investigation. It doesn't show what an operator actually needs mid-incident: what evidence has been gathered, what it points to, which hypotheses are competing.
- **Render the investigation's reasoning structure (selected)** — incident → evidence (one node per accumulated `TelemetryFindings`/`DeploymentFindings`/`KnowledgeContext`) → hypotheses, sized/colored by confidence and category. This grows and changes shape as the investigation runs, which is the actual value of a live canvas.

**Why:** an ops console's central visualization should earn its screen space by showing something that changes and matters per-incident. The fixed pipeline topology is better served by a dedicated, secondary `AgentActivityPanel` (which of the 8 nodes ran, in what order, per-node token/cost) — kept separate so neither view is asked to do both jobs.

**The hard part: evidence→hypothesis edges aren't a backend fact.** `Hypothesis.supporting_evidence`/`contradicting_evidence` are free-text prose written by the Incident Analysis agent — the schema doesn't link them to specific evidence IDs. Rather than invent a link or silently drop the edges, `lib/deriveEvidenceLinks.ts` draws an edge only when a real identifying token from an evidence node (a deployment's `version_to`/`deployment_id`, a metric `name`, a knowledge doc `title`) appears as a substring inside a hypothesis's citation text. An evidence node with no match still renders, connected only to the incident — visibly "gathered but not cited" rather than hidden or force-connected. This is documented inline in the derivation function as an explicit heuristic, not presented as backend-modeled truth.

---

## Decision 2: Client-side polling, not SSE/WebSockets

Investigations complete in well under a minute per the eval baseline (MTTFH < 5 min ship threshold, typically much faster). A `useInvestigationPolling` hook re-fetches `/status` + `/evidence` + `/analysis` + `/timeline` every ~1.5s while the investigation is active and stops on `complete`/`escalated`. This reads as live to an operator without touching the existing `202`-background-task execution model, adding a message broker, or managing SSE reconnection — all real cost for a latency budget this app doesn't have.

---

## Decision 3: Fixture replay runs through the real graph, not just `eval/runner.py`

The 3 eval fixtures previously only ran in-process via `unittest.mock.patch`, against `MemorySaver` — never through the live API or real Postgres checkpointer. That meant the Incident Library couldn't "launch" a fixture the same way it launches a live incident (`telemetry_node` hits live Cloud Monitoring, which has no data for a synthetic service).

`POST /v1/investigations/replay/{fixture_id}` reuses `eval.runner`'s existing mock-builder functions (`_build_mock_fetch_metrics`, etc. — imported, not duplicated) but wraps the real `app.state.investigation_graph` (real `AsyncPostgresSaver`) in that same `patch()` context. A replay is then indistinguishable from a live investigation to every other endpoint and to the frontend — same polling, same evidence/hypothesis rendering, same history list. **Documented limitation:** `patch()` is process-global, so two concurrent replays could theoretically cross-contaminate mocked calls. Acceptable for a single-operator demo tool; not engineered around.

---

## Decision 4: Hypothesis feedback — accept/reject record, challenge resumes

Accept/reject just appends a `HypothesisFeedback` to state via `graph.aupdate_state` — a signal captured for usability-test analysis, no resume. Challenge does the same append **and** resumes the graph (`aupdate_state(values={"phase": "planning"})` + a fresh `graph.ainvoke(None, config)`), reusing the same terminate-checkpoint-resume mechanics `submit_approval` already used for L3 actions. The planner's evidence-summary builder was extended with one additional block so it actually sees the human's note on its next iteration — the one piece of node logic this frontend effort touched, justified because "challenge" is required to steer a real re-investigation, not just record a click.

This resume path is **not** the same one found broken in `ADR-013`/`docs/KNOWN-ISSUES.md` #1. That gap is specific to `dispatcher_node`'s `dispatcher -> END` edge having no re-entry point for *L3 approval* resume. Challenge-resume re-enters at `planning`, upstream of `dispatcher`, so it isn't affected — confirmed by the fact that replayed challenge-resumes do visibly re-invoke the planner and produce new evidence. It's a coincidence worth noting explicitly so the two aren't conflated later: the codebase has one working checkpoint-resume path and one broken one, doing similar things at different points in the graph.

---

## Consequences

- The frontend has zero business logic of its own by design — types in `lib/types.ts` are hand-mapped 1:1 to backend Pydantic schemas (no OpenAPI codegen in this repo), and every screen is a read (or a thin feedback write) against `/v1/*`.
- `TelemetryFindings.log_events` is defined in the schema but never populated by `telemetry_node` or the eval fixtures. `LogExplorer.tsx` is built against the real `LogEvent` shape and renders an honest empty state rather than fabricated log lines — a real backend gap, not a frontend shortcut.
- Approving an L3-escalated investigation through `HumanControls.tsx` will update the UI but — per `docs/KNOWN-ISSUES.md` #1 — will not actually cause a dispatch. This is a pre-existing backend gap the frontend surfaces rather than causes; flagged here so a demo doesn't imply it works.
- **Layout lesson (worth recording so it isn't re-learned):** the Workspace's viewport-locked, IDE-style layout (fixed header, independently scrollable panels) initially looked correct by inspection — every panel had `flex-1 min-h-0` — but still let the page grow unbounded with a long Agent Activity history. Root cause: `flex-1`/`min-h-0` only bound a flex *item's* size; several wrapper `<div>`s (including inside the shared `Panel` component in `components/ui.tsx`) carried those classes without `display: flex` itself, so the bounded height never propagated to their children, which fell back to content-based sizing. Caught by rendering the real components with a synthetic 200-event timeline and inspecting actual `scrollHeight`/`clientHeight` in a browser, not by reading the JSX. Any new panel needs `flex flex-col` (not just `flex-1 min-h-0`) on every wrapping layer down to its scrollable content.
