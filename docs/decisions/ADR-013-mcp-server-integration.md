# ADR-013: MCP Server as a Thin HTTP Wrapper, Confirm-Gated Writes

**Status:** Accepted
**Date:** 2026-09-01

---

## Context

Phase 2 needed the investigation system's read/write capabilities exposed as tools an MCP client (Claude Desktop, Claude Code) can call directly - checking an investigation's status, searching the knowledge base, and eventually creating a ticket from a completed investigation - without a human manually working the REST API.

Two design questions came up immediately:

1. Should the MCP server call the FastAPI backend over HTTP, or import the graph/DB layer directly?
2. `create_ticket` is a write action. What stops an LLM client from creating a ticket the user never actually asked for?

---

## Decision 1: Thin HTTP wrapper over the running API, not direct DB/graph imports

**Options considered:**

- **Direct DB/graph calls** - `mcp_server/` imports `app.graph.nodes.knowledge._search_documents` and queries `investigations`/`incidents` directly. No dependency on a running API server, but duplicates session handling and can silently drift from what the real API actually does.
- **Thin HTTP wrapper (selected)** - each tool is an `httpx` call against the live FastAPI app (`GET /v1/investigations/{id}/status`, `POST /v1/knowledge/search`, `POST /v1/tickets`). Zero duplicated logic, tool behavior is guaranteed to match the API because it *is* the API - at the cost of requiring the backend to be running for the MCP server to do anything.

**Why:** the API is already the single source of truth for validation, error shapes, and DB access patterns (raw SQL via `text()`, no ORM layer). A second code path into the database would need to be kept in sync by hand. `mcp_server/` ended up as a standalone script + `requirements.txt` (not a package under `backend/app/`) specifically so it has no import-time coupling to `app.config.Settings` (which requires `DATABASE_URL`/`GCP_PROJECT_ID` to even instantiate) - it only needs `BACKEND_URL`.

**One endpoint didn't exist yet and got added to support this:** `POST /v1/knowledge/search` (`app/api/v1/knowledge.py`) - a thin wrapper around the existing `knowledge_node`'s `_search_documents()`, since that function previously only ran inside a live investigation via a planner-issued `KnowledgeQuery`, with no standalone HTTP entry point.

---

## Decision 2: Confirm-gate lives in the tool itself, not the existing L3 approval pattern

The system already has a human-approval mechanism for risky actions - Level 3 authority, terminate-checkpoint-resume (`ADR-004`). The natural instinct was to route `create_ticket` through it. Before doing that, the pattern was verified empirically (not just read): a synthetic `L3` hypothesis was run through the real `dispatcher_node` and the real `dispatcher -> END` edge, then driven through the exact `aupdate_state` + resume sequence `POST /v1/investigations/{id}/approval` uses.

**Result: it doesn't work.** `dispatched_actions` stayed empty across the "resume" - approving an L3 action today only flips `state["phase"]` to `"responding"`. The graph's `dispatcher -> END` edge is unconditional and nothing calls `interrupt()`, so once `dispatcher_node` decides not to dispatch, the graph is genuinely finished; there's no re-entry point left for a human's approval to resume into. (Full detail, plus a second, related finding - the `pending_approvals` DB table is never written to - in `docs/KNOWN-ISSUES.md`.)

**Options considered:**

- **Wire `create_ticket` into the L3 pattern anyway** - rejected. Would have required fixing a separate, non-trivial graph-topology bug (giving the graph a real pause/resume point) as a prerequisite, which is out of scope for adding a ticketing tool and deserves its own design pass.
- **Server-side draft + confirm endpoint** - `POST /v1/tickets` creates a `status: draft` row, a separate `POST /v1/tickets/{id}/confirm` flips it live. Durable and queryable, but needs schema/endpoint changes for a Phase 2 feature that's still a mocked destination.
- **Rely on the calling LLM's judgment, no gate in code** - simplest, but nothing stops a single bad tool call from creating a real ticket.
- **In-tool `confirm` flag (selected)** - `create_ticket(..., confirm: bool = False)`. A call with `confirm=False` (the default) creates nothing and returns a preview of what *would* be created; the tool's docstring instructs the calling model to show that preview to the user and only call again with `confirm=True` after explicit agreement. No backend changes, no new persistent state.

**Why:** this is a self-contained, Phase-2-appropriate mechanism that doesn't depend on fixing the L3 graph gap first, and it's independently testable (verified live: `confirm=False` makes zero HTTP calls to the backend; `confirm=True` makes the real `POST /v1/tickets` call). Verified end-to-end through Claude Desktop too - the model called with `confirm=False`, surfaced the preview, waited for the user's "yes", then called again with `confirm=True`.

---

## Consequences

- The MCP server has a hard runtime dependency on the FastAPI backend being reachable at `BACKEND_URL` (default `http://localhost:8080`) - it does nothing useful on its own.
- `create_ticket`'s confirmation is enforced by prompt/convention (the tool's docstring), not by the server refusing an unconfirmed write outright - a misbehaving or adversarial client could still pass `confirm=True` on the first call. Acceptable for a demo/portfolio tool; would need a server-side gate (e.g. the draft+confirm option above) for a production write surface.
- The L3 terminate-checkpoint-resume gap is now a tracked, documented issue (`docs/KNOWN-ISSUES.md`) rather than a silent trap the next feature might build on top of by assumption.
- `mcp_server/` pins `mcp<2` (the `mcp` SDK's 2.x line renamed `FastMCP` to `MCPServer` with a different API) - revisit if the project moves to the newer SDK.
