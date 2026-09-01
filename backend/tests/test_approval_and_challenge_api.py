"""
Endpoint-level tests for POST /{id}/approval and the "challenged" verdict on
POST /{id}/hypotheses/{hypothesis_id}/feedback (app/api/v1/investigations.py).

These test that the endpoints construct the right LangGraph resume call given
a snapshot's phase/next - not the LangGraph resume mechanics themselves (that's
covered by the real compiled-subgraph tests in test_dispatcher_node.py, and by
the standalone repros run during development). request.app.state.investigation_graph
is a bare AsyncMock here; no database is touched by either endpoint.

Needs DATABASE_URL/GCP_PROJECT_ID set (any value) purely because importing
app.api.v1.investigations pulls in app.db.session transitively - same situation
as test_tickets_api.py. Run alongside the other DB-importing test files:
    DATABASE_URL="..." GCP_PROJECT_ID="..." pytest tests/test_approval_and_challenge_api.py -v
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.types import Command

from app.api.v1.investigations import router as investigations_router

app = FastAPI()
app.include_router(investigations_router)
client = TestClient(app)


def _set_graph(snapshot_values: dict, snapshot_next: tuple) -> AsyncMock:
    graph = AsyncMock()
    graph.aget_state.return_value = SimpleNamespace(values=snapshot_values, next=snapshot_next)
    app.state.investigation_graph = graph
    return graph


# ── POST /{id}/approval ──────────────────────────────────────────────────────

def test_approval_rejects_when_not_awaiting_l3_gate():
    """phase='responding', snapshot.next=() -> not paused at the gate at all."""
    _set_graph({"phase": "responding"}, ())

    resp = client.post("/v1/investigations/inv-1/approval", json={
        "approved": True, "approved_by": "operator",
    })

    assert resp.status_code == 409


def test_approval_investigation_not_found():
    graph = AsyncMock()
    graph.aget_state.return_value = SimpleNamespace(values=None, next=())
    app.state.investigation_graph = graph

    resp = client.post("/v1/investigations/inv-missing/approval", json={
        "approved": True, "approved_by": "operator",
    })

    assert resp.status_code == 404


def test_approval_resumes_with_command_when_paused_at_gate():
    graph = _set_graph({"phase": "escalated"}, ("l3_approval_gate",))

    with patch("app.api.v1.investigations._run_investigation", AsyncMock()) as mock_run:
        resp = client.post("/v1/investigations/inv-1/approval", json={
            "approved": True, "approved_by": "operator", "notes": "go ahead",
        })

    assert resp.status_code == 200
    assert resp.json() == {"status": "approved", "investigation_id": "inv-1"}

    mock_run.assert_called_once()
    _, resume_arg, thread_config, investigation_id = mock_run.call_args[0]
    assert isinstance(resume_arg, Command)
    assert resume_arg.resume == {"approved": True, "approved_by": "operator", "notes": "go ahead"}
    assert investigation_id == "inv-1"
    assert thread_config == {"configurable": {"thread_id": "inv-1"}}


def test_approval_rejection_also_resumes_via_command():
    """Rejecting must also resume - l3_approval_gate_node itself decides not to
    dispatch and records the rejection; the old code never resumed at all here."""
    _set_graph({"phase": "escalated"}, ("l3_approval_gate",))

    with patch("app.api.v1.investigations._run_investigation", AsyncMock()) as mock_run:
        resp = client.post("/v1/investigations/inv-1/approval", json={
            "approved": False, "approved_by": "operator", "notes": "too risky",
        })

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    mock_run.assert_called_once()
    resume_arg = mock_run.call_args[0][1]
    assert resume_arg.resume == {"approved": False, "approved_by": "operator", "notes": "too risky"}


# ── POST /{id}/hypotheses/{id}/feedback (challenged) ────────────────────────

def test_challenge_rejected_while_investigating():
    _set_graph({"phase": "investigating"}, ())

    resp = client.post(
        "/v1/investigations/inv-1/hypotheses/h1/feedback",
        json={"verdict": "challenged", "submitted_by": "operator"},
    )

    assert resp.status_code == 409


def test_challenge_rejected_while_awaiting_l3_approval():
    _set_graph({"phase": "escalated"}, ("l3_approval_gate",))

    resp = client.post(
        "/v1/investigations/inv-1/hypotheses/h1/feedback",
        json={"verdict": "challenged", "submitted_by": "operator"},
    )

    assert resp.status_code == 409
    assert "L3 approval" in resp.json()["detail"]


def test_challenge_resumes_with_goto_planner_when_finished():
    _set_graph({"phase": "complete"}, ())

    with patch("app.api.v1.investigations._run_investigation", AsyncMock()) as mock_run:
        resp = client.post(
            "/v1/investigations/inv-1/hypotheses/h1/feedback",
            json={"verdict": "challenged", "submitted_by": "operator", "note": "check this again"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "reopened"

    mock_run.assert_called_once()
    _, resume_arg, _, _ = mock_run.call_args[0]
    assert isinstance(resume_arg, Command)
    assert resume_arg.goto == "planner"
    assert resume_arg.update == {"phase": "planning"}


def test_accept_reject_never_resumes():
    _set_graph({"phase": "complete"}, ())

    with patch("app.api.v1.investigations._run_investigation", AsyncMock()) as mock_run:
        resp = client.post(
            "/v1/investigations/inv-1/hypotheses/h1/feedback",
            json={"verdict": "accepted", "submitted_by": "operator"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "investigation_id": "inv-1", "verdict": "accepted"}
    mock_run.assert_not_called()
