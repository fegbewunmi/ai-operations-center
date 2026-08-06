"""
LangGraph node tracing helper.

Wraps each async node function so that every invocation produces a Cloud Trace span.
The span includes investigation_id, phase, and the node name as attributes —
making per-investigation flame graphs visible in Cloud Trace.
"""
import functools
from collections.abc import Callable, Awaitable

from opentelemetry import trace

tracer = trace.get_tracer("ai-ops-center.graph")


def traced_node(node_name: str):
    """
    Decorator that wraps an async graph node function with an OpenTelemetry span.

    Usage:
        @traced_node("planner")
        async def planner_node(state): ...
    """
    def decorator(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        @functools.wraps(fn)
        async def wrapper(state: dict) -> dict:
            investigation_id = state.get("investigation_id", "unknown")
            phase = state.get("phase", "unknown")

            with tracer.start_as_current_span(f"graph.{node_name}") as span:
                span.set_attribute("investigation.id", investigation_id)
                span.set_attribute("investigation.phase", phase)
                span.set_attribute("graph.node", node_name)

                result = await fn(state)

                # Record the resulting phase so traces show state transitions
                if isinstance(result, dict) and "phase" in result:
                    span.set_attribute("investigation.phase_after", result["phase"])

                return result

        return wrapper
    return decorator
