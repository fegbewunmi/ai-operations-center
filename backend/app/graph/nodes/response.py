from datetime import datetime, timezone

from app.graph.state import InvestigationState
from app.shared.schemas.core import TimelineEvent
from app.shared.schemas.response import DispatchedAction, PendingApproval


async def response_node(state: InvestigationState) -> dict:
    """
    Dispatches approved remediation actions and notifies stakeholders.
    Phase 1: creates a Slack notification record and marks investigation complete.
    L3 actions create a PendingApproval and keep the investigation open.
    """
    synthesis = state["synthesis"]
    top = synthesis.top_hypothesis
    investigation_id = state["investigation_id"]
    now = datetime.now(timezone.utc)

    dispatched: list[DispatchedAction] = []
    pending: list[PendingApproval] = []

    if top.authority_level == "L3":
        # Requires human approval - create a pending approval record
        approval = PendingApproval(
            approval_id=f"appr-{investigation_id[:8]}",
            investigation_id=investigation_id,
            action_description=top.recommended_action,
            authority_level="L3",
            checkpoint_id=investigation_id,
            requested_at=now,
        )
        pending.append(approval)
        phase = "escalated"
        description = f"L3 approval required: {top.recommended_action[:120]}"
    else:
        # L1/L2 - log as a Slack notification (Phase 1: no real dispatch)
        action = DispatchedAction(
            action_type="slack_message",
            external_id=f"slack-{investigation_id[:8]}",
            dispatched_at=now,
            authority_level=top.authority_level,  # type: ignore[arg-type]
        )
        dispatched.append(action)
        phase = "complete"
        description = f"Investigation complete. Action ({top.authority_level}): {top.recommended_action[:120]}"

    event = TimelineEvent(
        timestamp=now,
        event_type="action_dispatched",
        service=top.affected_service,
        description=description,
        source="response_dispatcher",
    )

    return {
        "dispatched_actions": dispatched,
        "pending_approvals": pending,
        "timeline": [event],
        "phase": phase,
        "completed_at": now,
    }
