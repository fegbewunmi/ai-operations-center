"""
Unit tests for the Incident Analysis Agent.

All LLM calls are mocked. No network, no database required.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.incident_analysis import incident_analysis_node
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.synthesis import AnalysisOutput, Hypothesis


def _make_hypothesis(confidence_pct: float = 88.0) -> Hypothesis:
    return Hypothesis(
        hypothesis_id="h1",
        description="Deployment of v2.1.0 caused 45% error spike",
        root_cause_category="deployment",
        affected_service="payments",
        confidence_pct=confidence_pct,
        supporting_evidence=["Deploy at T-15min", "Errors began after deploy"],
        contradicting_evidence=[],
        recommended_action="Roll back payments to v2.0.9",
        authority_level="L2",
    )


def _make_analysis_output(confidence_pct: float = 88.0) -> AnalysisOutput:
    hyp = _make_hypothesis(confidence_pct)
    return AnalysisOutput(
        hypotheses=[hyp],
        top_hypothesis=hyp,
        analysis_complete=True,
        requires_escalation=False,
    )


def _make_state(
    telemetry_summary: str = "Error rate spiked from 0.5% to 45% at 06:00 UTC",
    deployment_summary: str = "Deployment v2.1.0 deployed at 05:45 UTC (15 min before onset)",
) -> dict:
    incident = IncidentTrigger(
        incident_id="INC-001",
        alert_name="HighErrorRate",
        severity="P1",
        service_name="payments",
        onset_timestamp="2026-08-06T06:00:00Z",
        description="Payment service error rate spiked",
    )
    return {
        "investigation_id": "test-inv-001",
        "incident": incident,
        "phase": "synthesizing",
        "planner_decision": None,
        "planner_working_hypothesis": "Deployment caused error spike",
        "planner_working_confidence": 0.80,
        "budget": InvestigationBudget(iterations_used=3, tool_calls_used=3),
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


@patch("app.graph.nodes.incident_analysis.ChatVertexAI")
@pytest.mark.asyncio
async def test_produces_analysis_output(mock_llm_class):
    analysis = _make_analysis_output(confidence_pct=88.0)
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=analysis)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await incident_analysis_node(_make_state())

    assert result["analysis_output"] is analysis
    assert result["phase"] == "synthesizing"
    assert len(result["timeline"]) == 1
    assert result["timeline"][0].source == "incident_analysis"


@patch("app.graph.nodes.incident_analysis.ChatVertexAI")
@pytest.mark.asyncio
async def test_sets_phase_escalated_when_requires_escalation(mock_llm_class):
    hyp = _make_hypothesis(confidence_pct=25.0)
    analysis = AnalysisOutput(
        hypotheses=[hyp],
        top_hypothesis=hyp,
        requires_escalation=True,
        escalation_reason="Max confidence only 25% — insufficient evidence",
    )
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=analysis)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await incident_analysis_node(_make_state())

    assert result["phase"] == "escalated"
    assert result["escalation_reason"] is not None


@patch("app.graph.nodes.incident_analysis.ChatVertexAI")
@pytest.mark.asyncio
async def test_escalates_on_llm_failure(mock_llm_class):
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(side_effect=RuntimeError("Gemini timeout"))
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await incident_analysis_node(_make_state())

    assert result["phase"] == "escalated"
    assert len(result["error_log"]) == 1
    assert result["error_log"][0].agent == "incident_analysis"
    assert "Gemini timeout" in result["error_log"][0].message


@patch("app.graph.nodes.incident_analysis.ChatVertexAI")
@pytest.mark.asyncio
async def test_timeline_contains_confidence_in_description(mock_llm_class):
    analysis = _make_analysis_output(confidence_pct=88.0)
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=analysis)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    result = await incident_analysis_node(_make_state())

    description = result["timeline"][0].description
    assert "88" in description  # confidence_pct


@patch("app.graph.nodes.incident_analysis.ChatVertexAI")
@pytest.mark.asyncio
async def test_uses_structured_output_with_analysis_output_schema(mock_llm_class):
    """Confirm the node calls with_structured_output(AnalysisOutput), not SynthesisOutput."""
    analysis = _make_analysis_output()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=analysis)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_llm_class.return_value = mock_llm

    await incident_analysis_node(_make_state())

    mock_llm.with_structured_output.assert_called_once_with(AnalysisOutput)
