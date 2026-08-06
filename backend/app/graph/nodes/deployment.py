from datetime import datetime, timezone, timedelta
from typing import Any

from langchain_google_vertexai import ChatVertexAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.graph.state import InvestigationState
from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.deployment import DeploymentFindings, DeploymentRecord
from app.shared.schemas.planner import DeploymentQuery

# Deployment within this many minutes before onset is considered suspicious.
NEAR_ONSET_MINUTES = 30.0

# Look this many hours before the query window to catch slow-burn degradation.
PRE_WINDOW_BUFFER_HOURS = 2


async def fetch_deployments(
    service_name: str,
    window_start: datetime,
    window_end: datetime,
    onset_dt: datetime,
) -> list[DeploymentRecord]:
    """
    Query Cloud SQL for deployments of a service in a time window.
    Extends the window backward by PRE_WINDOW_BUFFER_HOURS to catch
    deployments that cause gradual rather than immediate degradation.
    Pure database operation - no LLM call. Tested directly.
    """
    buffered_start = window_start - timedelta(hours=PRE_WINDOW_BUFFER_HOURS)

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text("""
                SELECT
                    d.deployment_id::text,
                    s.name           AS service,
                    d.version_from,
                    d.version_to,
                    d.deployed_at,
                    d.deployed_by,
                    d.status,
                    d.config_changes,
                    d.rollback_available,
                    d.rollback_target_version,
                    d.git_commit_sha
                FROM deployments d
                JOIN services s ON s.service_id = d.service_id
                WHERE s.name = :service_name
                  AND d.deployed_at >= :start
                  AND d.deployed_at <= :end
                ORDER BY d.deployed_at DESC
            """),
            {
                "service_name": service_name,
                "start": buffered_start,
                "end": window_end,
            },
        )
        raw_rows = rows.mappings().all()

    records: list[DeploymentRecord] = []
    for row in raw_rows:
        deployed_at: datetime = row["deployed_at"]
        if deployed_at.tzinfo is None:
            deployed_at = deployed_at.replace(tzinfo=timezone.utc)

        onset_aware = onset_dt if onset_dt.tzinfo else onset_dt.replace(tzinfo=timezone.utc)
        minutes_before = (onset_aware - deployed_at).total_seconds() / 60.0

        config_changes: list[str] = row["config_changes"] or []

        records.append(DeploymentRecord(
            deployment_id=row["deployment_id"],
            service=row["service"],
            version_from=row["version_from"],
            version_to=row["version_to"],
            deployed_at=deployed_at,
            deployed_by=row["deployed_by"],
            status=row["status"],
            config_changes=config_changes,
            rollback_available=row["rollback_available"],
            rollback_target_version=row["rollback_target_version"],
            git_commit_sha=row["git_commit_sha"],
            minutes_before_onset=minutes_before,
        ))

    return records


async def generate_deployment_summary(
    deployments: list[DeploymentRecord],
    investigation_query: str,
    incident_description: str,
    llm: Any = None,
) -> str:
    """
    Ask Gemini to summarise deployment findings in relation to the incident.
    Accepts an optional llm parameter so tests can inject a mock.
    """
    if not deployments:
        return "No deployments found in the search window."

    if llm is None:
        llm = ChatVertexAI(
            model=settings.gemini_model,
            project=settings.gcp_project_id,
            location=settings.vertex_ai_location,
            temperature=0.1,
        )

    dep_lines = []
    for d in deployments:
        direction = "before" if (d.minutes_before_onset or 0) >= 0 else "after"
        mins = abs(d.minutes_before_onset or 0)
        changes = ", ".join(d.config_changes) if d.config_changes else "none recorded"
        dep_lines.append(
            f"  - {d.version_to} by {d.deployed_by} "
            f"({mins:.0f} min {direction} onset, status={d.status}, "
            f"changes: {changes})"
        )

    prompt = (
        f"Incident: {incident_description}\n"
        f"Investigation question: {investigation_query}\n\n"
        f"Deployments found:\n" + "\n".join(dep_lines) + "\n\n"
        "In 2-3 sentences: summarise whether these deployments are likely related "
        "to the incident, and why or why not."
    )

    response = await llm.ainvoke([
        SystemMessage(content="You are a site reliability engineer analysing deployment history."),
        HumanMessage(content=prompt),
    ])
    return response.content.strip()


async def deployment_node(state: InvestigationState) -> dict:
    """
    Queries deployment history for the service and time window specified by the Planner.
    Appends DeploymentFindings and a TimelineEvent to state.
    """
    decision = state["planner_decision"]
    query: DeploymentQuery = decision.query  # type: ignore[assignment]
    incident = state["incident"]

    onset_dt = (
        incident.onset_timestamp
        if isinstance(incident.onset_timestamp, datetime)
        else datetime.fromisoformat(str(incident.onset_timestamp))
    )

    try:
        records = await fetch_deployments(
            service_name=query.service_name,
            window_start=query.time_window.start,
            window_end=query.time_window.end,
            onset_dt=onset_dt,
        )
    except Exception as exc:
        return {
            "error_log": [AgentError(
                agent="deployment",
                query_summary=f"fetch deployments for {query.service_name}",
                error_type="tool_failure",
                message=str(exc),
                timestamp=datetime.now(timezone.utc),
                retries_attempted=0,
            )],
            "phase": "planning",
        }

    near = [r for r in records if 0 <= (r.minutes_before_onset or float("inf")) <= NEAR_ONSET_MINUTES]
    deployment_near_onset = len(near) > 0
    nearest_minutes = min((r.minutes_before_onset for r in near), default=None)

    try:
        summary = await generate_deployment_summary(
            deployments=records,
            investigation_query=query.investigation_query,
            incident_description=incident.description,
        )
    except Exception as exc:
        summary = f"Summary generation failed: {exc}"

    findings = DeploymentFindings(
        service=query.service_name,
        time_window=query.time_window,
        query=query.investigation_query,
        deployments=records,
        deployment_near_onset=deployment_near_onset,
        nearest_deployment_minutes=nearest_minutes,
        summary=summary,
    )

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="deployment",
        service=query.service_name,
        description=(
            f"Deployment check: {len(records)} deployment(s) found. "
            f"Near onset: {deployment_near_onset}."
            + (f" Nearest: {nearest_minutes:.0f} min before." if nearest_minutes is not None else "")
        ),
        source="deployment_agent",
    )

    return {
        "deployment_findings": [findings],
        "timeline": [event],
        "phase": "planning",
    }
