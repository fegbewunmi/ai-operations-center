from app.graph.state import InvestigationState


async def safety_guard_node(state: InvestigationState) -> dict:
    """
    Validates the Synthesizer's output before any action is dispatched.
    Checks: hypothesis confidence meets threshold, actions are within authority level,
    blast-radius limits respected, no conflicting actions.
    On failure, routes back to Planner with an error logged.
    On pass, routes to Response Dispatcher.
    """
    synthesis = state["synthesis"]

    # TODO: implement Safety Guard logic
    # - Verify synthesis.confidence_score >= budget.confidence_threshold
    # - For each proposed action, check action.authority_level vs incident classification
    # - Reject or flag any action that exceeds L1/L2 auto-dispatch limits
    # - Check blast_radius_estimate against per-action thresholds
    # - Detect conflicting actions (e.g., scale-up + scale-down same service)
    # - On validation failure: return {"validation_result": ..., "phase": "planning"} to re-enter Planner
    raise NotImplementedError("Safety Guard agent not yet implemented")
