"""
Unit tests for the Synthesizer Agent.

The synthesizer reads hypotheses from analysis_output and formats them into
a human-readable SynthesisOutput. LLM calls are mocked.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.synthesizer import synthesizer_node
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.synthesis import AnalysisOutput, Hypothesis


def _make_hypothesis() -> Hypothesis:
    return Hypothesis(
        hypothesis_id="h1",
        description="Deployment v2.1.0 caused 45% error spike",
        root_cause_category="deployment",
        affected_service="payments",
        confidence_pct=88.0,
        supporting_evidence=["Deploy at T-15min", "Error rate correlated"],
        contradicting_evidence=[],
        recommended_action="Roll back payments to v2.0.9",
        authority_level="L2",
    )


def _make_analysis(requires_escalation: bool = False) -> AnalysisOutput:
    hyp = _make_hypothesis()
    return AnalysisOutput(
        hypotheses=[hyp],
        top_hypothesis=hyp,
        requires_escalation=requires_escalation,
        escalation_reason="Low confidence" if requires_escalation else None,
    )


def _make_state(analysis_output: AnalysisOutput | None = None) -> dict:
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
        "phase": "synthesizing",
        "planner_decision": None,
        "planner_working_hypothesis": None,
        "planner_working_confidence": 0.0,
        "budget": InvestigationBudget(),
        "timeline": [],
        "telemetry_findings": [],
        "deployment_findings": [],
        "knowledge_context": [],
        "service_topology": None,
        "analysis_output": analysis_output,
        "synthesis": None,
        "validation_result": None,
        "dispatched_actions": [],
        "pending_approvals": [],
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }


@patch("app.graph.nodes.synthesizer.ChatVertexAI")
@pytest.mark.asyncio
async def test_produces_synthesis_from_analysis_output(mock_llm_class):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "The payment service was broken by a bad deploy. Roll it back."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    result = await synthesizer_node(_make_state(_make_analysis()))

    assert result["synthesis"] is not None
    assert result["synthesis"].incident_id == "INC-001"
    assert "bad deploy" in result["synthesis"].investigation_summary
    assert result["phase"] == "synthesizing"


@patch("app.graph.nodes.synthesizer.ChatVertexAI")
@pytest.mark.asyncio
async def test_passes_through_hypotheses_unchanged(mock_llm_class):
    """Synthesizer must not alter hypotheses — they came from analysis."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Summary text."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    analysis = _make_analysis()
    result = await synthesizer_node(_make_state(analysis))

    assert result["synthesis"].hypotheses == analysis.hypotheses
    assert result["synthesis"].top_hypothesis == analysis.top_hypothesis


@patch("app.graph.nodes.synthesizer.ChatVertexAI")
@pytest.mark.asyncio
async def test_passes_through_requires_escalation(mock_llm_class):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Insufficient evidence."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    analysis = _make_analysis(requires_escalation=True)
    result = await synthesizer_node(_make_state(analysis))

    assert result["synthesis"].requires_escalation is True
    assert result["synthesis"].escalation_reason == "Low confidence"


@patch("app.graph.nodes.synthesizer.ChatVertexAI")
@pytest.mark.asyncio
async def test_falls_back_to_mechanical_summary_on_llm_error(mock_llm_class):
    """On LLM failure, synthesizer falls back rather than escalating."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    mock_llm_class.return_value = mock_llm

    result = await synthesizer_node(_make_state(_make_analysis()))

    # Should still produce a synthesis (mechanical fallback), not escalate
    assert result["synthesis"] is not None
    assert result["phase"] == "synthesizing"
    assert "88" in result["synthesis"].investigation_summary  # confidence in fallback text


@pytest.mark.asyncio
async def test_escalates_when_analysis_output_is_none():
    result = await synthesizer_node(_make_state(analysis_output=None))

    assert result["phase"] == "escalated"
    assert len(result["error_log"]) == 1
    assert result["error_log"][0].agent == "synthesizer"


@patch("app.graph.nodes.synthesizer.ChatVertexAI")
@pytest.mark.asyncio
async def test_appends_timeline_event(mock_llm_class):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Summary."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    result = await synthesizer_node(_make_state(_make_analysis()))

    assert len(result["timeline"]) == 1
    assert result["timeline"][0].source == "synthesizer"
