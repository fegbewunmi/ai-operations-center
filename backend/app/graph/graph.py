from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.deployment import deployment_node
from app.graph.nodes.incident_analysis import incident_analysis_node
from app.graph.nodes.knowledge import knowledge_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.dispatcher import dispatcher_node, l3_approval_gate_node
from app.graph.nodes.safety_guard import safety_guard_node
from app.graph.nodes.synthesizer import synthesizer_node
from app.graph.nodes.telemetry import telemetry_node
from app.graph.routing import route_from_dispatcher, route_from_planner, route_from_safety_guard
from app.graph.state import InvestigationState


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> StateGraph:
    """
    Assembles the investigation graph and compiles it.

    Accepts an optional checkpointer so tests can pass MemorySaver and
    production can pass PostgresSaver without changing the graph definition.

    Graph topology:

        START
          |
        planner  <-----------+-------+-------+
          |                  |       |       |
          +-- invoke ------> telemetry  deployment  knowledge
          |                  (each loops back to planner)
          |
          +-- synthesize --> incident_analysis   <-- NEW
          |                      |
          |                  synthesizer
          |                      |
          |                 safety_guard
          |                  /        \\
          |           passed /          \\ failed
          |                /            \\
          |          dispatcher        planner (re-plan)
          |           /      \\
          |     L1/L2         L3
          |        |            \\
          +-- escalate --> END   l3_approval_gate --(interrupt, resume via
                           ^                          POST /approval)--> END
                           |
                      dispatcher (after dispatching)
    """
    builder = StateGraph(InvestigationState)

    # --- Nodes ---
    builder.add_node("planner", planner_node)
    builder.add_node("telemetry", telemetry_node)
    builder.add_node("deployment", deployment_node)
    builder.add_node("knowledge", knowledge_node)
    builder.add_node("incident_analysis", incident_analysis_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("safety_guard", safety_guard_node)
    builder.add_node("dispatcher", dispatcher_node)
    builder.add_node("l3_approval_gate", l3_approval_gate_node)

    # --- Edges ---

    builder.add_edge(START, "planner")

    # Planner routes to a specialist, incident_analysis, or END (escalation / budget)
    builder.add_conditional_edges(
        "planner",
        route_from_planner,
        {
            "telemetry": "telemetry",
            "deployment": "deployment",
            "knowledge": "knowledge",
            "incident_analysis": "incident_analysis",
            END: END,
        },
    )

    # Each specialist loops back to the planner for the next decision
    builder.add_edge("telemetry", "planner")
    builder.add_edge("deployment", "planner")
    builder.add_edge("knowledge", "planner")

    # Incident analysis flows to synthesizer (formatting only)
    builder.add_edge("incident_analysis", "synthesizer")

    # Synthesizer always feeds the safety guard
    builder.add_edge("synthesizer", "safety_guard")

    # Safety guard either dispatches or sends the planner back to re-plan
    builder.add_conditional_edges(
        "safety_guard",
        route_from_safety_guard,
        {
            "dispatcher": "dispatcher",
            "planner": "planner",
        },
    )

    # Dispatcher terminates immediately for L1/L2 (already dispatched); an L3
    # hypothesis routes to a dedicated gate node that pauses for human approval
    # instead of dispatcher itself reaching END (see ADR-015).
    builder.add_conditional_edges(
        "dispatcher",
        route_from_dispatcher,
        {
            "l3_approval_gate": "l3_approval_gate",
            END: END,
        },
    )
    builder.add_edge("l3_approval_gate", END)

    return builder.compile(checkpointer=checkpointer)


# Module-level graph instance used by the FastAPI app.
# PostgresSaver is injected at application startup in main.py.
investigation_graph = build_graph()
