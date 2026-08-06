from app.graph.state import InvestigationState
from app.shared.schemas.planner import PlannerDecision


async def planner_node(state: InvestigationState) -> dict:
    """
    Controls the investigation loop.
    Decides which specialist to invoke next, or when to synthesize.
    Reads the full InvestigationState and emits a PlannerDecision.
    """
    # TODO: implement Planner agent logic
    # - Fetch service topology on first iteration if not present
    # - Review accumulated findings
    # - Update working hypothesis and confidence
    # - Decide next action based on remaining budget and evidence gaps
    raise NotImplementedError("Planner agent not yet implemented")
