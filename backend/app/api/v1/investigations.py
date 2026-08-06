import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import AsyncSessionLocal, get_db
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.response import PendingApproval

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/investigations", tags=["investigations"])


# --- Request / Response models ---

class StartInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    incident: IncidentTrigger


class StartInvestigationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: str
    status: str
    message: str


class InvestigationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: str
    phase: str
    working_hypothesis: str | None
    working_confidence: float
    iterations_used: int
    tool_calls_used: int
    started_at: datetime
    completed_at: datetime | None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    approved_by: str
    notes: str | None = None


# --- Helpers ---

async def _persist_incident_and_investigation(
    incident_db_id: str,
    investigation_id: str,
    incident: IncidentTrigger,
) -> None:
    """
    Write incidents + investigations rows before starting the graph.
    incident_db_id is a fresh UUID (the IncidentTrigger.incident_id may be a
    human-readable string like "INC-001" that won't fit a UUID primary key).
    """
    metadata = {"original_id": incident.incident_id, **incident.alert_metadata}
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO incidents (
                    incident_id, alert_name, severity, service_name,
                    onset_timestamp, description, alert_metadata
                ) VALUES (
                    :incident_id::uuid, :alert_name, :severity, :service_name,
                    :onset_timestamp::timestamptz, :description, :alert_metadata::jsonb
                ) ON CONFLICT (incident_id) DO NOTHING
            """),
            {
                "incident_id": incident_db_id,
                "alert_name": incident.alert_name,
                "severity": incident.severity,
                "service_name": incident.service_name,
                "onset_timestamp": incident.onset_timestamp,
                "description": incident.description,
                "alert_metadata": json.dumps(metadata),
            },
        )
        await db.execute(
            text("""
                INSERT INTO investigations (investigation_id, incident_id, phase)
                VALUES (:investigation_id::uuid, :incident_id::uuid, 'planning')
            """),
            {"investigation_id": investigation_id, "incident_id": incident_db_id},
        )
        await db.commit()


async def _run_investigation(
    graph,
    initial_state: dict,
    thread_config: dict,
    investigation_id: str,
) -> None:
    """Background task: run the graph; mark escalated in DB on unhandled error."""
    try:
        await graph.ainvoke(initial_state, config=thread_config)
    except Exception as exc:
        logger.error("Investigation %s failed unexpectedly: %s", investigation_id, exc, exc_info=True)
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE investigations
                    SET phase = 'escalated', completed_at = NOW()
                    WHERE investigation_id = :id::uuid
                """),
                {"id": investigation_id},
            )
            await db.commit()


# --- Endpoints ---

@router.post("", response_model=StartInvestigationResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_investigation(
    body: StartInvestigationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StartInvestigationResponse:
    """
    Trigger a new investigation. Returns 202 immediately; the graph runs in the background.
    Poll /status to track progress.
    """
    graph = request.app.state.investigation_graph
    investigation_id = str(uuid.uuid4())
    incident_db_id = str(uuid.uuid4())
    thread_config = {"configurable": {"thread_id": investigation_id}}

    await _persist_incident_and_investigation(incident_db_id, investigation_id, body.incident)

    initial_state = {
        "investigation_id": investigation_id,
        "incident": body.incident,
        "phase": "planning",
        "planner_decision": None,
        "planner_working_hypothesis": None,
        "planner_working_confidence": 0.0,
        "budget": InvestigationBudget(
            max_iterations=settings.max_iterations,
            max_tool_calls=settings.max_tool_calls,
            max_tokens=settings.max_tokens,
            confidence_threshold=settings.confidence_threshold,
        ),
        "timeline": [],
        "telemetry_findings": [],
        "deployment_findings": [],
        "knowledge_context": [],
        "service_topology": None,
        "analysis_output": None,
        "synthesis": None,
        "validation_result": None,
        "dispatched_actions": [],
        "pending_approvals": [],
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }

    asyncio.create_task(
        _run_investigation(graph, initial_state, thread_config, investigation_id)
    )

    return StartInvestigationResponse(
        investigation_id=investigation_id,
        status="accepted",
        message="Investigation started. Poll /status for progress.",
    )


@router.get("/{investigation_id}/status", response_model=InvestigationStatusResponse)
async def get_investigation_status(
    investigation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvestigationStatusResponse:
    """Return the current phase and budget usage for an investigation."""
    graph = request.app.state.investigation_graph
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    state = snapshot.values
    budget = state["budget"]

    return InvestigationStatusResponse(
        investigation_id=investigation_id,
        phase=state["phase"],
        working_hypothesis=state.get("planner_working_hypothesis"),
        working_confidence=state.get("planner_working_confidence", 0.0),
        iterations_used=budget.iterations_used,
        tool_calls_used=budget.tool_calls_used,
        started_at=state["started_at"],
        completed_at=state.get("completed_at"),
    )


@router.get("/{investigation_id}/findings")
async def get_findings(
    investigation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return synthesis output for a completed investigation."""
    graph = request.app.state.investigation_graph
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    state = snapshot.values
    synthesis = state.get("synthesis")

    if synthesis is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Investigation is in phase '{state['phase']}', synthesis not yet complete",
        )

    return synthesis.model_dump()


@router.get("/{investigation_id}/timeline")
async def get_timeline(
    investigation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the chronological event timeline for an investigation."""
    graph = request.app.state.investigation_graph
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    state = snapshot.values
    return {
        "investigation_id": investigation_id,
        "events": [e.model_dump() for e in state.get("timeline", [])],
    }


@router.post("/{investigation_id}/approval", status_code=status.HTTP_200_OK)
async def submit_approval(
    investigation_id: str,
    body: ApprovalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Submit a human approval decision for an L3-escalated investigation.
    On approval, resumes the graph from its saved checkpoint.
    """
    graph = request.app.state.investigation_graph
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    state = snapshot.values

    if state["phase"] != "escalated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Investigation is in phase '{state['phase']}', not awaiting approval",
        )

    if not body.approved:
        await graph.aupdate_state(
            config=thread_config,
            values={"escalation_reason": f"Rejected by {body.approved_by}: {body.notes}"},
        )
        return {"status": "rejected", "investigation_id": investigation_id}

    await graph.aupdate_state(
        config=thread_config,
        values={"phase": "responding"},
    )
    asyncio.create_task(
        _run_investigation(graph, None, thread_config, investigation_id)
    )
    return {"status": "approved", "investigation_id": investigation_id}
