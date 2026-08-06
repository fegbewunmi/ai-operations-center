from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.deployment import deployment_node
from app.graph.nodes.knowledge import knowledge_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.response import response_node
from app.graph.nodes.safety_guard import safety_guard_node
from app.graph.nodes.synthesizer import synthesizer_node
from app.graph.nodes.telemetry import telemetry_node
from app.graph.routing import route_from_planner, route_from_safety_guard
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
          +-- invoke -------> telemetry  deployment  knowledge
          |                  (each loops back to planner)
          |
          +-- synthesize --> synthesizer
          |                      |
          |                 safety_guard
          |                  /        \
          |           passed /          \ failed
          |                /            \
          |           response         planner (re-plan)
          |                |
          +-- escalate --> END
                           ^
                           |
                       response (after dispatching)
    """
    builder = StateGraph(InvestigationState)

    # --- Nodes ---
    builder.add_node("planner", planner_node)
    builder.add_node("telemetry", telemetry_node)
    builder.add_node("deployment", deployment_node)
    builder.add_node("knowledge", knowledge_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("safety_guard", safety_guard_node)
    builder.add_node("response", response_node)

    # --- Edges ---

    # Entry point
    builder.add_edge(START, "planner")

    # Planner routes to a specialist, the synthesizer, or END (escalation / budget exhausted)
    builder.add_conditional_edges(
        "planner",
        route_from_planner,
        {
            "telemetry": "telemetry",
            "deployment": "deployment",
            "knowledge": "knowledge",
            "synthesizer": "synthesizer",
            END: END,
        },
    )

    # Each specialist loops back to the planner for the next decision
    builder.add_edge("telemetry", "planner")
    builder.add_edge("deployment", "planner")
    builder.add_edge("knowledge", "planner")

    # Synthesizer always feeds the safety guard
    builder.add_edge("synthesizer", "safety_guard")

    # Safety guard either dispatches or sends the planner back to re-plan
    builder.add_conditional_edges(
        "safety_guard",
        route_from_safety_guard,
        {
            "response": "response",
            "planner": "planner",
        },
    )

    # Response dispatcher terminates the graph
    builder.add_edge("response", END)

    return builder.compile(checkpointer=checkpointer)


# Module-level graph instance used by the FastAPI app.
# PostgresSaver is injected at application startup in main.py.
investigation_graph = build_graph()
