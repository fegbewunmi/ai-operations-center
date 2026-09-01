"""
MCP server exposing Trace's incident-investigation API as tools for MCP clients
(Claude Desktop, Claude Code, etc).

Thin HTTP wrapper around the FastAPI backend (backend/app/main.py) — no direct
DB/graph access — so tool behavior always matches the live API. Requires the
backend to already be running (see backend/README.md).

Run: python server.py
"""
import os
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")

mcp = FastMCP("trace-incidents")


@mcp.tool()
async def get_investigation_status(investigation_id: str) -> dict:
    """
    Get the current status of an incident investigation: its triggering incident,
    phase, working hypothesis, confidence, and budget usage.

    Args:
        investigation_id: The investigation's UUID, returned when it was started
            via POST /v1/investigations or /v1/investigations/replay/{fixture_id}.
    """
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
        resp = await client.get(f"/v1/investigations/{investigation_id}/status")

    if resp.status_code == 404:
        raise ValueError(f"No investigation found with id '{investigation_id}'")
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def search_documents(
    query: str,
    document_type: str | None = None,
    service_name: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Semantic search over the knowledge base — runbooks, postmortems, architecture
    docs, and known error patterns — for content relevant to an incident. Use
    this to find remediation steps, prior root causes, or system context for a
    symptom or error, e.g. "connection pool exhaustion" or "payment gateway
    timeout".

    Args:
        query: Natural-language description of the symptom, error, or question
            to search for.
        document_type: Restrict results to one type: "runbook", "postmortem",
            "architecture_doc", or "error_pattern". Omit to search all types.
        service_name: Restrict results to documents scoped to one service (docs
            with no service scope are still included). Omit to search all
            services.
        limit: Max number of results to return (default 5).
    """
    payload: dict = {"query": query, "limit": limit}
    if document_type is not None:
        payload["document_types"] = [document_type]
    if service_name is not None:
        payload["service_name"] = service_name

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
        resp = await client.post("/v1/knowledge/search", json=payload)

    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def get_incident_history() -> list[dict]:
    """
    List past incident investigations, both live and fixture replays, most
    recent first (up to 100). Each entry has the incident's alert name, service,
    severity, current phase, and whether it was live or replayed. Use this to
    check whether a similar incident has occurred before, or to find an
    investigation_id to pass to get_investigation_status.
    """
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
        resp = await client.get("/v1/investigations")

    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def create_ticket(
    title: str,
    severity: Literal["P1", "P2", "P3"],
    investigation_id: str,
    description: str | None = None,
    confirm: bool = False,
) -> dict:
    """
    Create a ticket for an investigation in the mocked ticketing system. This
    is a WRITE action, gated behind explicit confirmation:

    - Call with confirm=False (the default) first. This creates nothing and
      returns a preview of what would be created. Show that preview to the
      user and get their explicit go-ahead.
    - Only call again with confirm=True, using the same arguments, after the
      user has agreed. That call actually creates the ticket.

    Never call with confirm=True on the first attempt.

    Args:
        title: Short ticket title/summary.
        severity: One of "P1", "P2", "P3".
        investigation_id: The investigation this ticket is for, e.g. from
            get_investigation_status or get_incident_history.
        description: Optional longer description — root cause, recommended
            action, etc.
        confirm: False (default) previews without creating anything. True
            actually creates the ticket — only pass this after the user has
            confirmed the preview.
    """
    preview = {
        "title": title,
        "description": description,
        "severity": severity,
        "investigation_id": investigation_id,
    }

    if not confirm:
        return {
            "status": "preview",
            "message": (
                "Nothing was created. Show this preview to the user, and only "
                "call create_ticket again with confirm=True after they agree."
            ),
            "would_create": preview,
        }

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
        resp = await client.post("/v1/tickets", json=preview)

    if resp.status_code == 404:
        raise ValueError(f"No investigation found with id '{investigation_id}'")
    resp.raise_for_status()
    return {"status": "created", "ticket": resp.json()}


if __name__ == "__main__":
    mcp.run()
