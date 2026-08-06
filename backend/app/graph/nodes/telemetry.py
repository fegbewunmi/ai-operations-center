import asyncio
from datetime import datetime, timezone
from typing import Any

from google.cloud import monitoring_v3
from google.protobuf.timestamp_pb2 import Timestamp
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.graph.state import InvestigationState
from app.graph.tracing import traced_node
from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.planner import TelemetryQuery
from app.shared.schemas.telemetry import MetricPoint, TelemetryFindings

# Standard Cloud Monitoring metric types to check for a service.
# These cover Cloud Run and custom application metrics.
_METRIC_TEMPLATES = [
    "run.googleapis.com/request_count",
    "run.googleapis.com/request_latencies",
    "run.googleapis.com/container/cpu/utilizations",
    "run.googleapis.com/container/memory/utilizations",
]


def _to_proto_timestamp(dt: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
    return ts


def _fetch_metrics_sync(
    project_id: str,
    service_name: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[MetricPoint], str | None]:
    """
    Query Cloud Monitoring for known metric types for this service.
    Runs synchronously - called via asyncio.to_thread.
    Returns (metrics, error_message).
    """
    client = monitoring_v3.MetricServiceClient()
    project_name = f"projects/{project_id}"

    interval = monitoring_v3.TimeInterval(
        start_time=_to_proto_timestamp(window_start),
        end_time=_to_proto_timestamp(window_end),
    )

    metrics: list[MetricPoint] = []

    for metric_type in _METRIC_TEMPLATES:
        filter_str = (
            f'metric.type="{metric_type}" AND '
            f'resource.labels.service_name="{service_name}"'
        )
        try:
            results = client.list_time_series(
                request={
                    "name": project_name,
                    "filter": filter_str,
                    "interval": interval,
                    "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                }
            )
            for series in results:
                unit = series.unit or "1"
                for point in series.points:
                    val = (
                        point.value.double_value
                        or point.value.int64_value
                        or point.value.distribution_value.mean
                        if hasattr(point.value, "distribution_value")
                        else point.value.double_value
                    )
                    pt_time = point.interval.end_time.ToDatetime(tzinfo=timezone.utc)
                    metrics.append(MetricPoint(
                        name=metric_type.split("/")[-1],
                        value=float(val),
                        unit=unit,
                        timestamp=pt_time,
                        is_anomalous=False,
                        baseline_value=None,
                    ))
        except Exception:
            # This metric type doesn't exist for this service - skip silently
            pass

    return metrics, None


async def _fetch_cloud_monitoring_metrics(
    service_name: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[MetricPoint], str | None]:
    try:
        return await asyncio.to_thread(
            _fetch_metrics_sync,
            settings.gcp_project_id,
            service_name,
            window_start,
            window_end,
        )
    except Exception as exc:
        return [], f"Cloud Monitoring query failed: {exc}"


async def _generate_telemetry_summary(
    metrics: list[MetricPoint],
    query: str,
    incident_description: str,
    service_name: str,
    error: str | None,
    llm: Any = None,
) -> str:
    if llm is None:
        llm = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
            temperature=0.1,
        )

    if error:
        context = f"Cloud Monitoring query error: {error}"
    elif not metrics:
        context = (
            f"No metrics found in Cloud Monitoring for service '{service_name}' "
            f"in the requested time window. This may mean the service is not "
            f"instrumented in Cloud Monitoring, uses a different service name label, "
            f"or no traffic was recorded."
        )
    else:
        metric_lines = [
            f"  - {m.name}: {m.value:.3f} {m.unit} at {m.timestamp.isoformat()}"
            for m in metrics[:20]
        ]
        context = "Metrics retrieved:\n" + "\n".join(metric_lines)

    prompt = (
        f"Incident: {incident_description}\n"
        f"Investigation question: {query}\n\n"
        f"Telemetry findings:\n{context}\n\n"
        "In 2-3 sentences: summarise what the telemetry data reveals about the incident. "
        "If no data was found, explain what that implies and what other signals to investigate."
    )

    response = await llm.ainvoke([
        SystemMessage(content="You are an SRE analysing telemetry data during an incident."),
        HumanMessage(content=prompt),
    ])
    return response.content.strip()


@traced_node("telemetry")
async def telemetry_node(state: InvestigationState) -> dict:
    """
    Queries Cloud Monitoring metrics for the service and time window specified by the Planner.
    Appends TelemetryFindings and a TimelineEvent to state.
    """
    decision = state["planner_decision"]
    query: TelemetryQuery = decision.query  # type: ignore[assignment]
    incident = state["incident"]

    window_start = query.time_window.start
    window_end = query.time_window.end

    try:
        metrics, fetch_error = await _fetch_cloud_monitoring_metrics(
            service_name=query.service_name,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        return {
            "error_log": [AgentError(
                agent="telemetry",
                query_summary=f"fetch metrics for {query.service_name}",
                error_type="tool_failure",
                message=str(exc),
                timestamp=datetime.now(timezone.utc),
                retries_attempted=0,
            )],
            "phase": "planning",
        }

    anomalous = [m for m in metrics if m.is_anomalous]

    try:
        summary = await _generate_telemetry_summary(
            metrics=metrics,
            query=query.investigation_query,
            incident_description=incident.description,
            service_name=query.service_name,
            error=fetch_error,
        )
    except Exception:
        if not metrics:
            summary = f"No Cloud Monitoring data found for {query.service_name}. Service may not be instrumented or uses a different metric label."
        else:
            summary = f"Retrieved {len(metrics)} metric points but summary generation unavailable."

    findings = TelemetryFindings(
        service=query.service_name,
        time_window=query.time_window,
        query=query.investigation_query,
        key_metrics=metrics[:50],
        anomalous_metrics=anomalous,
        summary=summary,
        error=fetch_error,
    )

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=query.service_name,
        description=(
            f"Telemetry check: {len(metrics)} metric point(s) found. "
            + (f"Anomalous: {len(anomalous)}." if metrics else "No Cloud Monitoring data.")
        ),
        source="telemetry_agent",
    )

    return {
        "telemetry_findings": [findings],
        "timeline": [event],
        "phase": "planning",
    }
