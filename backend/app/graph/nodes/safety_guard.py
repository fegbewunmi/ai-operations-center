from datetime import datetime, timezone

from app.config import settings
from app.graph.state import InvestigationState
from app.shared.schemas.core import TimelineEvent
from app.shared.schemas.validation import ValidationResult


async def safety_guard_node(state: InvestigationState) -> dict:
    """
    Validates the Synthesizer's output before any action is dispatched.
    Phase 1: passes through if top hypothesis confidence meets threshold.
    """
    synthesis = state["synthesis"]
    budget = state["budget"]

    top = synthesis.top_hypothesis
    confidence_ok = top.confidence_pct >= (budget.confidence_threshold * 100)

    passed = confidence_ok and not synthesis.requires_escalation

    issues = []
    if not confidence_ok:
        issues.append(
            f"Confidence {top.confidence_pct:.0f}% below threshold "
            f"{budget.confidence_threshold * 100:.0f}%"
        )
    if synthesis.requires_escalation:
        issues.append(synthesis.escalation_reason or "Synthesizer flagged escalation required")

    result = ValidationResult(
        passed=passed,
        issues=issues,
        risk_level=top.authority_level,
        investigation_incomplete=synthesis.investigation_incomplete,
    )

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=state["incident"].service_name,
        description=(
            f"Safety guard: {'PASSED' if passed else 'FAILED'}"
            + (f" - {'; '.join(issues)}" if issues else " - all checks passed")
        ),
        source="safety_guard",
    )

    return {
        "validation_result": result,
        "timeline": [event],
        "phase": "responding" if passed else "escalated",
    }
