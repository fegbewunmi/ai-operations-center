from typing import Literal

from langgraph.graph import END

from app.graph.state import InvestigationState

# Return types for LangGraph conditional edge functions
PlannerRoute = Literal["telemetry", "deployment", "knowledge", "synthesizer", "__end__"]
GuardRoute = Literal["response", "planner"]


def route_from_planner(state: InvestigationState) -> PlannerRoute:
    """
    Conditional edge after the Planner node.

    Decision priority:
      1. Budget exhausted -> END (Planner must have set phase="escalated")
      2. decision.action == "escalate" -> END
      3. decision.action == "synthesize" -> synthesizer
      4. decision.action == "invoke" -> named specialist
    """
    if state["budget"].is_exhausted:
        return END  # type: ignore[return-value]

    decision = state["planner_decision"]
    if decision is None:
        # Should not happen in normal flow; treat as escalation
        return END  # type: ignore[return-value]

    if decision.action == "escalate":
        return END  # type: ignore[return-value]

    if decision.action == "synthesize":
        return "synthesizer"

    # decision.action == "invoke"
    agent = decision.agent
    if agent in ("telemetry", "deployment", "knowledge"):
        return agent  # type: ignore[return-value]

    # Unrecognised agent name - escalate rather than loop forever
    return END  # type: ignore[return-value]


def route_from_safety_guard(state: InvestigationState) -> GuardRoute:
    """
    Conditional edge after the Safety Guard node.

    Pass -> response dispatcher.
    Fail -> back to planner so it can attempt a revised synthesis.
    """
    validation = state.get("validation_result")
    if validation is not None and validation.passed:
        return "response"
    return "planner"
