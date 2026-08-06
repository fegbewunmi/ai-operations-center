"""
Unit tests for the Safety Guard node.

No LLM calls — safety_guard is pure logic over InvestigationState.
All test cases run without a database or network.

The safety guard runs four checks:
  1. Confidence — meets configured threshold
  2. Evidence grounding — ≥2 substantive supporting items
  3. Source alignment — hypothesis category matches gathered evidence
  4. Authority — L3 actions have a clear directive
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
    supporting_evidence: list[str] | None = None,
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id="h1",
        description="Payment service deployed v2.1.0 caused 45% error rate spike",
        root_cause_category="deployment",
        affected_service="payments",
        confidence_pct=confidence_pct,
        supporting_evidence=supporting_evidence or [
            "Error rate spiked from 0.5% to 45% at 06:00 UTC per Cloud Monitoring",
            "Payments v2.1.0 deployed at 05:45 UTC, exactly 15 minutes before error onset",
        ],
        contradicting_evidence=[],
        recommended_action="Roll back payments service to v2.0.9",
        authority_level=authority_level,  # type: ignore[arg-type]
    )


def _make_synthesis(
    confidence_pct: float = 90.0,
    authority_level: str = "L2",
    requires_escalation: bool = False,
    supporting_evidence: list[str] | None = None,
) -> SynthesisOutput:
    hyp = _make_hypothesis(confidence_pct, authority_level, supporting_evidence=supporting_evidence)
    return SynthesisOutput(
        incident_id="INC-001",
        investigation_summary="A deployment caused the error spike.",
        hypotheses=[hyp],
        top_hypothesis=hyp,
        requires_escalation=requires_escalation,
        escalation_reason="Insufficient evidence" if requires_escalation else None,
    )


def _make_state(
    synthesis: SynthesisOutput,
    confidence_threshold: float = 0.90,
    telemetry_findings: list | None = None,
    deployment_findings: list | None = None,
    knowledge_context: list | None = None,
) -> dict:
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
        "telemetry_findings": telemetry_findings if telemetry_findings is not None else [],
        "deployment_findings": deployment_findings if deployment_findings is not None else [],
        "knowledge_context": knowledge_context if knowledge_context is not None else [],
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


# Shared minimal evidence for pass-path tests (deployment root cause needs both)
_TELEMETRY = ["error_rate=45% at 06:00 UTC, baseline 0.5%"]
_DEPLOYMENT = ["payments v2.1.0 deployed at 05:45 UTC, 15 min before onset"]


# ── Pass cases ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_passes_when_all_checks_clear():
    synthesis = _make_synthesis(confidence_pct=90.0)
    result = await safety_guard_node(_make_state(
        synthesis,
        confidence_threshold=0.90,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    vr = result["validation_result"]
    assert vr.passed is True
    assert vr.confidence_ok is True
    assert vr.evidence_grounded is True
    assert vr.sources_aligned is True
    assert vr.authority_ok is True
    assert result["phase"] == "responding"


@pytest.mark.asyncio
async def test_passes_when_confidence_exceeds_threshold():
    synthesis = _make_synthesis(confidence_pct=95.0)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    assert result["validation_result"].passed is True


@pytest.mark.asyncio
async def test_l3_authority_passes_with_clear_action():
    """L3 authority with a specific recommended_action should pass the authority check."""
    synthesis = _make_synthesis(confidence_pct=92.0, authority_level="L3", requires_escalation=False)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    assert result["validation_result"].passed is True
    assert result["phase"] == "responding"


# ── Check 1: Confidence ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fails_when_confidence_below_threshold():
    synthesis = _make_synthesis(confidence_pct=75.0)
    result = await safety_guard_node(_make_state(
        synthesis,
        confidence_threshold=0.90,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    vr = result["validation_result"]
    assert vr.passed is False
    assert vr.confidence_ok is False
    assert result["phase"] == "escalated"
    assert any("75%" in issue or "75" in issue for issue in vr.issues)


@pytest.mark.asyncio
async def test_fails_when_requires_escalation_flag_set():
    synthesis = _make_synthesis(confidence_pct=92.0, requires_escalation=True)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    assert result["validation_result"].passed is False
    assert result["phase"] == "escalated"


@pytest.mark.asyncio
async def test_fails_when_both_low_confidence_and_escalation_required():
    synthesis = _make_synthesis(confidence_pct=40.0, requires_escalation=True)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    vr = result["validation_result"]
    assert vr.passed is False
    assert any("40" in issue for issue in vr.issues)
    # escalation_reason from fixture is "Insufficient evidence"
    assert any("escalat" in issue.lower() or "insufficient" in issue.lower() for issue in vr.issues)


# ── Check 2: Evidence grounding ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fails_when_only_one_evidence_item():
    synthesis = _make_synthesis(
        confidence_pct=92.0,
        supporting_evidence=["Error rate spiked at 06:00 UTC per Cloud Monitoring"],
    )
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    vr = result["validation_result"]
    assert vr.passed is False
    assert vr.evidence_grounded is False
    assert any("Insufficient" in issue for issue in vr.issues)


@pytest.mark.asyncio
async def test_fails_when_evidence_items_are_trivial():
    synthesis = _make_synthesis(
        confidence_pct=92.0,
        supporting_evidence=["errors", "deploy"],  # both < 10 chars
    )
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    vr = result["validation_result"]
    assert vr.passed is False
    assert vr.evidence_grounded is False


# ── Check 3: Source alignment ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fails_when_no_telemetry_gathered():
    synthesis = _make_synthesis(confidence_pct=92.0)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=[],   # no telemetry
        deployment_findings=_DEPLOYMENT,
    ))
    vr = result["validation_result"]
    assert vr.passed is False
    assert vr.sources_aligned is False
    assert any("telemetry" in issue.lower() for issue in vr.issues)


@pytest.mark.asyncio
async def test_fails_when_deployment_category_without_deployment_evidence():
    synthesis = _make_synthesis(confidence_pct=92.0)
    assert synthesis.top_hypothesis.root_cause_category == "deployment"
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=[],  # deployment agent never invoked
    ))
    vr = result["validation_result"]
    assert vr.passed is False
    assert vr.sources_aligned is False
    assert any("deployment agent" in issue.lower() for issue in vr.issues)


# ── Check 4: Authority ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fails_when_l3_action_has_no_recommended_action():
    hyp = Hypothesis(
        hypothesis_id="h1",
        description="Major infrastructure failure",
        root_cause_category="infrastructure",
        affected_service="payments",
        confidence_pct=92.0,
        supporting_evidence=[
            "Error rate spiked from 0.5% to 45% at 06:00 UTC per Cloud Monitoring",
            "All downstream services reporting timeouts simultaneously",
        ],
        contradicting_evidence=[],
        recommended_action="",  # missing directive
        authority_level="L3",
    )
    synthesis = SynthesisOutput(
        incident_id="INC-001",
        investigation_summary="Major infra failure.",
        hypotheses=[hyp],
        top_hypothesis=hyp,
    )
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        knowledge_context=["Infrastructure runbook: check control plane health"],
    ))
    vr = result["validation_result"]
    assert vr.passed is False
    assert vr.authority_ok is False
    assert any("L3" in issue or "directive" in issue.lower() for issue in vr.issues)


# ── Timeline ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_always_appends_timeline_event():
    synthesis = _make_synthesis(confidence_pct=92.0)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    assert len(result["timeline"]) == 1
    assert result["timeline"][0].source == "safety_guard"


@pytest.mark.asyncio
async def test_timeline_event_says_passed_on_success():
    synthesis = _make_synthesis(confidence_pct=92.0)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    assert "PASSED" in result["timeline"][0].description


@pytest.mark.asyncio
async def test_timeline_event_says_failed_on_failure():
    synthesis = _make_synthesis(confidence_pct=50.0)
    result = await safety_guard_node(_make_state(synthesis))
    assert "FAILED" in result["timeline"][0].description


# ── ValidationResult per-check fields ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_validation_result_has_per_check_breakdown():
    synthesis = _make_synthesis(confidence_pct=92.0)
    result = await safety_guard_node(_make_state(
        synthesis,
        telemetry_findings=_TELEMETRY,
        deployment_findings=_DEPLOYMENT,
    ))
    vr = result["validation_result"]
    assert hasattr(vr, "confidence_ok")
    assert hasattr(vr, "evidence_grounded")
    assert hasattr(vr, "sources_aligned")
    assert hasattr(vr, "authority_ok")
