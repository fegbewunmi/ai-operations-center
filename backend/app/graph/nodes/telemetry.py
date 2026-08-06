from datetime import datetime, timezone

from app.graph.state import InvestigationState
from app.shared.schemas.core import TimelineEvent
from app.shared.schemas.telemetry import TelemetryFindings


async def telemetry_node(state: InvestigationState) -> dict:
    """
    Queries metrics, logs, and traces for a specific service and time window.
    Reads TelemetryQuery from state["planner_decision"].query.
    Appends TelemetryFindings and a TimelineEvent to state.
    """
    query = state["planner_decision"].query  # TelemetryQuery

    # TODO: implement Telemetry agent logic
    # - Call Cloud Monitoring for metrics
    # - Call Cloud Logging for log events
    # - Detect anomalies against baseline
    # - Generate LLM summary of findings
    # - Wrap results in TelemetryFindings
    raise NotImplementedError("Telemetry agent not yet implemented")
