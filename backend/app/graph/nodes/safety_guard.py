from datetime import datetime, timezone

from app.graph.state import InvestigationState
from app.graph.tracing import traced_node
from app.shared.schemas.core import TimelineEvent
from app.shared.schemas.synthesis import Hypothesis, SynthesisOutput
from app.shared.schemas.validation import ValidationResult


# ── Check 1: Confidence ────────────────────────────────────────────────────────

def _check_confidence(top: Hypothesis, threshold_pct: float) -> list[str]:
    if top.confidence_pct < threshold_pct:
        return [
            f"Confidence {top.confidence_pct:.0f}% is below the "
            f"{threshold_pct:.0f}% required threshold"
        ]
    return []


# ── Check 2: Evidence grounding ────────────────────────────────────────────────

def _check_evidence_grounding(top: Hypothesis) -> list[str]:
    """Top hypothesis must have ≥2 supporting evidence items, each substantive."""
    issues = []
    evidence = top.supporting_evidence or []

    if len(evidence) < 2:
        issues.append(
            f"Insufficient supporting evidence: {len(evidence)} item(s), minimum 2 required"
        )

    # Evidence items shorter than 10 chars are almost certainly placeholder text
    trivial = [e for e in evidence if len(e.strip()) < 10]
    if trivial:
        issues.append(
            f"{len(trivial)} evidence item(s) lack specificity — "
            "each must describe a concrete, observable signal"
        )

    return issues


# ── Check 3: Source alignment ──────────────────────────────────────────────────

def _check_source_alignment(top: Hypothesis, state: InvestigationState) -> list[str]:
    """Hypothesis category must be consistent with actually gathered evidence."""
    issues = []
    category = top.root_cause_category

    has_telemetry = bool(state.get("telemetry_findings"))
    has_deployment = bool(state.get("deployment_findings"))
    has_knowledge = bool(state.get("knowledge_context"))

    if not has_telemetry:
        issues.append(
            "No telemetry evidence gathered — hypotheses must be grounded in observed signals"
        )

    if category == "deployment" and not has_deployment:
        issues.append(
            "Deployment root cause claimed but the deployment agent was never invoked"
        )

    if category in ("configuration", "infrastructure") and not has_knowledge:
        issues.append(
            f"Root cause '{category}' typically requires runbook or postmortem context — "
            "knowledge agent was not consulted"
        )

    return issues


# ── Check 4: Authority ────────────────────────────────────────────────────────

def _check_authority(synthesis: SynthesisOutput) -> list[str]:
    """L3 authority actions must carry a specific, actionable directive."""
    top = synthesis.top_hypothesis
    if top.authority_level == "L3":
        if not top.recommended_action or len(top.recommended_action.strip()) < 10:
            return [
                "L3 authority action requires a specific recommended_action — "
                "cannot escalate without a clear directive"
            ]
    return []


# ── Node ──────────────────────────────────────────────────────────────────────

@traced_node("safety_guard")
async def safety_guard_node(state: InvestigationState) -> dict:
    """
    Validates synthesis output before any action is dispatched.

    Runs four checks in sequence:
      1. Confidence — meets configured threshold
      2. Evidence grounding — ≥2 substantive supporting items
      3. Source alignment — hypothesis category matches gathered evidence
      4. Authority — L3 actions have a clear directive

    All checks must pass. On failure, routes back to the planner with the
    specific issues so it can gather additional evidence before re-synthesizing.
    """
    synthesis = state["synthesis"]
    budget = state["budget"]
    top = synthesis.top_hypothesis

    threshold_pct = budget.confidence_threshold * 100

    confidence_issues  = _check_confidence(top, threshold_pct)
    evidence_issues    = _check_evidence_grounding(top)
    source_issues      = _check_source_alignment(top, state)
    authority_issues   = _check_authority(synthesis)

    escalation_issues: list[str] = []
    if synthesis.requires_escalation:
        escalation_issues = [
            synthesis.escalation_reason or "Synthesizer flagged escalation required"
        ]

    all_issues = (
        confidence_issues
        + evidence_issues
        + source_issues
        + authority_issues
        + escalation_issues
    )

    passed = not all_issues

    result = ValidationResult(
        passed=passed,
        issues=all_issues,
        risk_level=top.authority_level,
        confidence_ok=not confidence_issues,
        evidence_grounded=not evidence_issues,
        sources_aligned=not source_issues,
        authority_ok=not authority_issues,
        investigation_incomplete=synthesis.investigation_incomplete,
    )

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=state["incident"].service_name,
        description=(
            f"Safety guard: {'PASSED' if passed else 'FAILED'}"
            + (f" — {'; '.join(all_issues)}" if all_issues else " — all checks passed")
        ),
        source="safety_guard",
    )

    return {
        "validation_result": result,
        "timeline": [event],
        "phase": "responding" if passed else "escalated",
    }
