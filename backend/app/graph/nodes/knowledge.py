from app.graph.state import InvestigationState


async def knowledge_node(state: InvestigationState) -> dict:
    """
    Retrieves relevant runbooks, postmortems, and architecture docs via pgvector.
    Also fetches service ownership via the Service Catalog tool.
    Reads KnowledgeQuery from state["planner_decision"].query.
    Appends KnowledgeContext to state.
    """
    query = state["planner_decision"].query  # KnowledgeQuery

    # TODO: implement Knowledge agent logic
    # - Generate query embedding via Vertex AI text-embedding-004
    # - Run pgvector similarity search against documents table
    # - Fetch service ownership from service_ownership table (Service Catalog tool)
    # - Phase 2: query incident_memory for similar past incidents
    # - Generate LLM summary of retrieved context
    raise NotImplementedError("Knowledge agent not yet implemented")
