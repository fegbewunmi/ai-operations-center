from app.graph.state import InvestigationState


async def synthesizer_node(state: InvestigationState) -> dict:
    """
    Produces ranked root-cause hypotheses from the complete evidence set.
    Called exactly once per investigation, when the Planner decides evidence is sufficient.
    Writes SynthesisOutput to state.
    """
    # TODO: implement Synthesizer agent logic
    # - Assemble full evidence context from state (timeline, all findings, topology)
    # - Generate ranked hypotheses with supporting/contradicting evidence
    # - Assign confidence scores and authority levels
    # - Build investigation summary
    # - Populate IncidentMemoryRecord (Phase 2: written to DB after Safety Guard passes)
    raise NotImplementedError("Synthesizer agent not yet implemented")
