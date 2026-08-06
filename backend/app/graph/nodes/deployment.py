from app.graph.state import InvestigationState


async def deployment_node(state: InvestigationState) -> dict:
    """
    Retrieves deployment history and config changes near the incident onset.
    Reads DeploymentQuery from state["planner_decision"].query.
    Appends DeploymentFindings and a TimelineEvent to state.
    """
    query = state["planner_decision"].query  # DeploymentQuery

    # TODO: implement Deployment agent logic
    # - Query deployments table for the service and time window
    # - Calculate minutes_before_onset for each deployment
    # - Set deployment_near_onset flag
    # - Generate LLM summary of findings
    raise NotImplementedError("Deployment agent not yet implemented")
