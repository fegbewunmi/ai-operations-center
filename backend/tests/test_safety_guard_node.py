"""
Unit tests for the Safety Guard node.

No LLM calls — safety_guard is pure logic over InvestigationState.
All test cases run without a database or network.
"""
from datetime import datetime, timezone

import pytest

from app.graph.nodes.safety_guard import safety_guard_node
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.synthesis import AnalysisOutput, Hypothesis, SynthesisOutput


def _make_hypothesis(
    confidence_pct: float = 90.0,
    authority_level: str = "L2",
    requires_escalation: bool = False,
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id="h1",
        description="Payment service deployed v2.1.0 caused 45% error rate spike",
        root_cause_category="deployment",
        affected_service="payments",
        confidence_pct=confidence_pct,
        supporting_evidence=["Deployment at T-15min", "Error rate correlated with deploy"],
        contradicting_evidence=[],
        recommended_action="Roll back payments to v2.0.9",
        authority_level=authority_level,  # type: ignore[arg-type]
    )


def _make_synthesis(
    confidence_pct: float = 90.0,
    authority_level: str = "L2",
    requires_escalation: bool = False,
) -> SynthesisOutput:
    hyp = _make_hypothesis(confidence_pct, authority_level)
    return SynthesisOutput(
        incident_id="INC-001",
        investigation_summary="A deployment caused the error spike.",
        hypotheses=[hyp],
        top_hypothesis=hyp,
        requires_escalation=requires_escalation,
        escalation_reason="Insufficient evidence" if requires_escalation else None,
    )


def _make_state(synthesis: SynthesisOutput, confidence_threshold: float = 0.90) -> dict:
    incident = IncidentTrigger(
        incident_id="INC-001",
        alert_name="HighErrorRate",
        severity="P1",
        service_name="payments",
        onset_timestamp="2026-08-06T06:00:00Z",
        description="Error rate spiked",
    )
    budget = InvestigationBudget(
        confidence_threshold=confidence_threshold,
        iterations_used=2,
        tool_calls_used=3,
    )
    return {
        "investigation_id": "test-inv-001",
        "incident": incident,
        "phase": "synthesizing",
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
        "synthesis": synthesis,
        "validation_result": None,
        "dispatched_actions": [],
        "pending_approvals": [],
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }


# ── Pass cases ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_passes_when_confidence_meets_threshold():
    synthesis = _make_synthesis(confidence_pct=90.0)
    result = await safety_guard_node(_make_state(synthesis, confidence_threshold=0.90))

    assert result["validation_result"].passed is True
    assert result["phase"] == "responding"


@pytest.mark.asyncio
async def test_passes_when_confidence_exceeds_threshold():
    synthesis = _make_synthesis(confidence_pct=95.0)
    result = await safety_guard_node(_make_state(synthesis, confidence_threshold=0.90))

    assert result["validation_result"].passed is True


@pytest.mark.asyncio
async def test_l3_authority_does_not_auto_fail():
    """L3 authority level alone does not cause failure — only requires_escalation does."""
    synthesis = _make_synthesis(confidence_pct=92.0, authority_level="L3", requires_escalation=False)
    result = await safety_guard_node(_make_state(synthesis))

    assert result["validation_result"].passed is True
    assert result["phase"] == "responding"


# ── Fail cases ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fails_when_confidence_below_threshold():
    synthesis = _make_synthesis(confidence_pct=75.0)
    result = await safety_guard_node(_make_state(synthesis, confidence_threshold=0.90))

    assert result["validation_result"].passed is False
    assert result["phase"] == "escalated"
    assert any("75%" in issue or "75" in issue for issue in result["validation_result"].issues)


@pytest.mark.asyncio
async def test_fails_when_requires_escalation_flag_set():
    synthesis = _make_synthesis(confidence_pct=92.0, requires_escalation=True)
    result = await safety_guard_node(_make_state(synthesis))

    assert result["validation_result"].passed is False
    assert result["phase"] == "escalated"


@pytest.mark.asyncio
async def test_fails_when_both_low_confidence_and_escalation_required():
    synthesis = _make_synthesis(confidence_pct=40.0, requires_escalation=True)
    result = await safety_guard_node(_make_state(synthesis))

    assert result["validation_result"].passed is False
    assert len(result["validation_result"].issues) == 2


# ── Timeline ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_always_appends_timeline_event():
    synthesis = _make_synthesis(confidence_pct=92.0)
    result = await safety_guard_node(_make_state(synthesis))

    assert len(result["timeline"]) == 1
    assert result["timeline"][0].source == "safety_guard"


@pytest.mark.asyncio
async def test_timeline_event_says_passed_on_success():
    synthesis = _make_synthesis(confidence_pct=92.0)
    result = await safety_guard_node(_make_state(synthesis))

    assert "PASSED" in result["timeline"][0].description


@pytest.mark.asyncio
async def test_timeline_event_says_failed_on_failure():
    synthesis = _make_synthesis(confidence_pct=50.0)
    result = await safety_guard_node(_make_state(synthesis))

    assert "FAILED" in result["timeline"][0].description
