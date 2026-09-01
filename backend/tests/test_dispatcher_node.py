"""
Tests for the Action Dispatcher / L3 approval gate (app/graph/nodes/dispatcher.py).

Covers the fix for the L3 approval dead-end documented in ADR-015 /
docs/KNOWN-ISSUES.md #1: dispatcher_node used to reach a genuine END for L3
hypotheses, leaving nothing for POST /approval's aupdate_state + ainvoke(None)
resume to continue into. dispatcher_node now routes an L3 decision to a
dedicated l3_approval_gate_node that pauses via LangGraph's interrupt(), which
POST /approval resumes with Command(resume=...) — verified here against the
real compiled sub-graph, not just by inspection.

Unit tests (no DB, no LangGraph engine) mock the two DB helpers directly.
The subgraph test uses a real compiled StateGraph + MemorySaver to prove the
pause/resume mechanics against the actual production node functions.
The @integration test proves the DB helpers' raw SQL against a real Postgres —
this was also manually verified against a scratch DB during development.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from app.graph.nodes.dispatcher import dispatcher_node, l3_approval_gate_node
from app.graph.routing import route_from_dispatcher
from app.graph.state import InvestigationState
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.synthesis import Hypothesis, SynthesisOutput
from tests.conftest import integration


def _make_hypothesis(authority_level: str = "L2") -> Hypothesis:
    return Hypothesis(
        hypothesis_id="h1",
        description="Payments v2.1.0 caused the error spike",
        root_cause_category="deployment",
        affected_service="payments",
        confidence_pct=90.0,
        supporting_evidence=["Error rate spiked at 06:00 UTC"],
        contradicting_evidence=[],
        recommended_action="Roll back payments to v2.0.9",
        authority_level=authority_level,  # type: ignore[arg-type]
    )


def _make_state(authority_level: str = "L2") -> dict:
    hyp = _make_hypothesis(authority_level)
    synthesis = SynthesisOutput(
        incident_id="INC-001",
        investigation_summary="A deployment caused the error spike.",
        hypotheses=[hyp],
        top_hypothesis=hyp,
        requires_escalation=False,
        escalation_reason=None,
    )
    incident = IncidentTrigger(
        incident_id="INC-001",
        alert_name="HighErrorRate",
        severity="P1",
        service_name="payments",
        onset_timestamp="2026-08-06T06:00:00Z",
        description="Error rate spiked",
    )
    return {
        "investigation_id": "test-inv-001",
        "incident": incident,
        "phase": "responding",
        "planner_decision": None,
        "planner_working_hypothesis": None,
        "planner_working_confidence": 0.0,
        "budget": InvestigationBudget(iterations_used=2, tool_calls_used=3),
        "timeline": [],
        "telemetry_findings": [],
        "deployment_findings": [],
        "knowledge_context": [],
        "service_topology": None,
        "analysis_output": None,
        "synthesis": synthesis,
        "validation_result": None,
        "dispatched_actions": [],
        "pending_approvals": [],
        "human_feedback": [],
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }


# ── dispatcher_node unit tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatcher_l1_l2_dispatches_immediately():
    with patch("app.graph.nodes.dispatcher._write_incident_memory", AsyncMock()) as mock_write:
        result = await dispatcher_node(_make_state(authority_level="L2"))

    assert len(result["dispatched_actions"]) == 1
    assert result["dispatched_actions"][0].authority_level == "L2"
    assert result["phase"] == "complete"
    assert result["completed_at"] is not None
    mock_write.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatcher_l3_does_not_dispatch_creates_pending_approval():
    with patch("app.graph.nodes.dispatcher._create_pending_approval", AsyncMock()) as mock_create:
        result = await dispatcher_node(_make_state(authority_level="L3"))

    assert "dispatched_actions" not in result
    assert result["phase"] == "escalated"
    assert "completed_at" not in result  # not actually complete yet
    mock_create.assert_awaited_once_with("test-inv-001", "Roll back payments to v2.0.9")


# ── l3_approval_gate_node unit tests (interrupt() mocked to its resume value) ──

@pytest.mark.asyncio
async def test_l3_gate_approved_dispatches():
    state = _make_state(authority_level="L3")
    with (
        patch("app.graph.nodes.dispatcher.interrupt", return_value={"approved": True, "approved_by": "op", "notes": None}),
        patch("app.graph.nodes.dispatcher._resolve_pending_approval", AsyncMock()) as mock_resolve,
        patch("app.graph.nodes.dispatcher._write_incident_memory", AsyncMock()) as mock_write,
    ):
        result = await l3_approval_gate_node(state)

    assert result["phase"] == "complete"
    assert result["dispatched_actions"][0].authority_level == "L3"
    assert result["completed_at"] is not None
    mock_resolve.assert_awaited_once()
    assert mock_resolve.call_args[0][:2] == ("test-inv-001", True)
    mock_write.assert_awaited_once()


@pytest.mark.asyncio
async def test_l3_gate_rejected_does_not_dispatch():
    state = _make_state(authority_level="L3")
    with (
        patch("app.graph.nodes.dispatcher.interrupt", return_value={"approved": False, "approved_by": "op", "notes": "too risky"}),
        patch("app.graph.nodes.dispatcher._resolve_pending_approval", AsyncMock()) as mock_resolve,
        patch("app.graph.nodes.dispatcher._write_incident_memory", AsyncMock()) as mock_write,
    ):
        result = await l3_approval_gate_node(state)

    assert result["phase"] == "escalated"
    assert "dispatched_actions" not in result
    assert "too risky" in result["timeline"][0].description
    mock_resolve.assert_awaited_once()
    assert mock_resolve.call_args[0][:2] == ("test-inv-001", False)
    mock_write.assert_not_awaited()


# ── Subgraph test: real interrupt()/Command(resume=...) mechanics ───────────

def _build_dispatch_subgraph():
    """
    A minimal graph wiring the real dispatcher_node -> route_from_dispatcher ->
    l3_approval_gate_node exactly as build_graph() does, so this proves the
    pause/resume mechanics against production code without running the full
    planner/specialist pipeline.
    """
    builder = StateGraph(InvestigationState)
    builder.add_node("dispatcher", dispatcher_node)
    builder.add_node("l3_approval_gate", l3_approval_gate_node)
    builder.set_entry_point("dispatcher")
    builder.add_conditional_edges(
        "dispatcher",
        route_from_dispatcher,
        {"l3_approval_gate": "l3_approval_gate", END: END},
    )
    builder.add_edge("l3_approval_gate", END)
    return builder.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_l3_subgraph_pauses_then_resumes_on_approval():
    graph = _build_dispatch_subgraph()
    config = {"configurable": {"thread_id": "sub-1"}}

    with patch("app.graph.nodes.dispatcher._create_pending_approval", AsyncMock()):
        await graph.ainvoke(_make_state(authority_level="L3"), config=config)

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("l3_approval_gate",), "graph should be paused at the gate, not finished"
    assert snapshot.values["phase"] == "escalated"

    with (
        patch("app.graph.nodes.dispatcher._resolve_pending_approval", AsyncMock()),
        patch("app.graph.nodes.dispatcher._write_incident_memory", AsyncMock()),
    ):
        await graph.ainvoke(
            Command(resume={"approved": True, "approved_by": "operator", "notes": None}),
            config=config,
        )

    final = await graph.aget_state(config)
    assert final.next == ()
    assert final.values["phase"] == "complete"
    assert len(final.values["dispatched_actions"]) == 1
    assert final.values["dispatched_actions"][0].authority_level == "L3"


@pytest.mark.asyncio
async def test_l3_subgraph_pauses_then_resumes_on_rejection():
    graph = _build_dispatch_subgraph()
    config = {"configurable": {"thread_id": "sub-2"}}

    with patch("app.graph.nodes.dispatcher._create_pending_approval", AsyncMock()):
        await graph.ainvoke(_make_state(authority_level="L3"), config=config)

    with patch("app.graph.nodes.dispatcher._resolve_pending_approval", AsyncMock()) as mock_resolve:
        await graph.ainvoke(
            Command(resume={"approved": False, "approved_by": "operator", "notes": "not now"}),
            config=config,
        )

    final = await graph.aget_state(config)
    assert final.next == ()
    assert final.values["phase"] == "escalated"
    assert final.values["dispatched_actions"] == []
    mock_resolve.assert_awaited_once()


# ── Integration: real Postgres round-trip for the two DB helpers ────────────

@integration
@pytest.mark.asyncio
async def test_pending_approval_create_and_resolve_against_real_db(investigation_factory):
    from app.graph.nodes.dispatcher import _create_pending_approval, _resolve_pending_approval
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    investigation_id = await investigation_factory(phase="responding")

    await _create_pending_approval(investigation_id, "Roll back payments to v2.0.9")

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("SELECT approved, action_description FROM pending_approvals WHERE investigation_id = :id ::uuid"),
            {"id": investigation_id},
        )).fetchall()
    assert len(rows) == 1
    assert rows[0].approved is None
    assert rows[0].action_description == "Roll back payments to v2.0.9"

    await _resolve_pending_approval(investigation_id, True, "operator", "lgtm", datetime.now(timezone.utc))

    async with AsyncSessionLocal() as db:
        rows2 = (await db.execute(
            text("SELECT approved, approved_by, notes FROM pending_approvals WHERE investigation_id = :id ::uuid"),
            {"id": investigation_id},
        )).fetchall()
    assert rows2[0].approved is True
    assert rows2[0].approved_by == "operator"
    assert rows2[0].notes == "lgtm"
