"""
Unit tests for the Planner Agent.

The planner has two external dependencies: Gemini (LLM) and Cloud SQL (topology).
Both are mocked here. No network or database required.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.planner import planner_node
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.planner import DeploymentQuery, PlannerDecision
from app.shared.schemas.core import TimeWindow


def _make_incident() -> IncidentTrigger:
    return IncidentTrigger(
        incident_id="INC-001",
        alert_name="HighErrorRate",
        severity="P1",
        service_name="payments",
        onset_timestamp="2026-08-06T06:00:00Z",
        description="Payment service error rate spiked",
    )


def _make_state(
    iterations_used: int = 0,
    tool_calls_used: int = 0,
    max_iterations: int = 5,
    max_tool_calls: int = 20,
) -> dict:
    budget = InvestigationBudget(
        max_iterations=max_iterations,
        max_tool_calls=max_tool_calls,
        iterations_used=iterations_used,
        tool_calls_used=tool_calls_used,
    )
    return {
        "investigation_id": "test-inv-001",
        "incident": _make_incident(),
        "phase": "planning",
        "planner_decision": None,
        "planner_working_hypothesis": None,
        "planner_working_confidence": 0.0,
        "budget": budget,
        "timeline": [],
        "telemetry_findings": [],
        "deployment_findings": [],
        "knowledge_context": [],
        "service_topology": None,
        "analysis_output": None,
        "synthesis": None,
        "validation_result": None,
        "dispatched_actions": [],
        "pending_approvals": [],
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }


# ── Budget exhaustion ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalates_immediately_when_budget_exhausted():
    """Planner must not call the LLM when iteration budget is already zero."""
    state = _make_state(iterations_used=5, max_iterations=5)

    with patch("app.graph.nodes.planner.ChatVertexAI") as mock_cls:
        result = await planner_node(state)
        mock_cls.assert_not_called()

    assert result["phase"] == "escalated"
    assert result["planner_decision"].action == "escalate"


@pytest.mark.asyncio
async def test_escalates_when_tool_calls_exhausted():
    state = _make_state(tool_calls_used=20, max_tool_calls=20)

    with patch("app.graph.nodes.planner.ChatVertexAI"):
        result = await planner_node(state)

    assert result["phase"] == "escalated"


# ── LLM-driven routing ─────────────────────────────────────────────────────────

@patch("app.graph.nodes.planner._fetch_topology", new_callable=AsyncMock)
@patch("app.graph.nodes.planner.ChatVertexAI")
@pytest.mark.asyncio
async def test_invoke_decision_sets_investigating_phase(mock_llm_class, mock_topology):
    mock_topology.return_value = None
    decision = PlannerDecision(
        action="invoke",
        agent="telemetry",
        query=DeploymentQuery(
            service_name="payments",
            time_window=TimeWindow(
                start=datetime(2026, 8, 6, 5, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc),
            ),
            investigation_query="check metrics",
        ),
        reason="Start with telemetry to establish the symptom baseline",
        working_hypothesis=None,
        working_confidence=0.0,
    )
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=decision)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await planner_node(_make_state())

    assert result["phase"] == "investigating"
    assert result["planner_decision"].action == "invoke"
    assert result["planner_decision"].agent == "telemetry"


@patch("app.graph.nodes.planner._fetch_topology", new_callable=AsyncMock)
@patch("app.graph.nodes.planner.ChatVertexAI")
@pytest.mark.asyncio
async def test_synthesize_decision_sets_synthesizing_phase(mock_llm_class, mock_topology):
    mock_topology.return_value = None
    decision = PlannerDecision(
        action="synthesize",
        reason="All evidence gathered; confidence sufficient",
        working_hypothesis="Deployment caused error spike",
        working_confidence=0.88,
    )
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=decision)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await planner_node(_make_state())

    assert result["phase"] == "synthesizing"
    assert result["planner_working_hypothesis"] == "Deployment caused error spike"
    assert result["planner_working_confidence"] == pytest.approx(0.88)


@patch("app.graph.nodes.planner._fetch_topology", new_callable=AsyncMock)
@patch("app.graph.nodes.planner.ChatVertexAI")
@pytest.mark.asyncio
async def test_escalate_decision_sets_escalated_phase(mock_llm_class, mock_topology):
    mock_topology.return_value = None
    decision = PlannerDecision(
        action="escalate",
        reason="Cannot determine root cause with available evidence",
        working_hypothesis=None,
        working_confidence=0.0,
    )
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=decision)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await planner_node(_make_state())

    assert result["phase"] == "escalated"
    assert result["escalation_reason"] is not None


# ── Error handling ─────────────────────────────────────────────────────────────

@patch("app.graph.nodes.planner._fetch_topology", new_callable=AsyncMock)
@patch("app.graph.nodes.planner.ChatVertexAI")
@pytest.mark.asyncio
async def test_escalates_on_llm_failure(mock_llm_class, mock_topology):
    mock_topology.return_value = None
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(side_effect=RuntimeError("Gemini timeout"))
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await planner_node(_make_state())

    assert result["phase"] == "escalated"
    assert len(result["error_log"]) == 1
    assert result["error_log"][0].agent == "planner"


# ── Budget increments ──────────────────────────────────────────────────────────

@patch("app.graph.nodes.planner._fetch_topology", new_callable=AsyncMock)
@patch("app.graph.nodes.planner.ChatVertexAI")
@pytest.mark.asyncio
async def test_increments_budget_on_successful_decision(mock_llm_class, mock_topology):
    mock_topology.return_value = None
    decision = PlannerDecision(
        action="invoke",
        agent="deployment",
        query=DeploymentQuery(
            service_name="payments",
            time_window=TimeWindow(
                start=datetime(2026, 8, 6, 5, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc),
            ),
            investigation_query="check deployments",
        ),
        reason="Check deployment history",
    )
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=decision)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    state = _make_state(iterations_used=1, tool_calls_used=2)
    result = await planner_node(state)

    assert result["budget"].iterations_used == 2
    assert result["budget"].tool_calls_used == 3
