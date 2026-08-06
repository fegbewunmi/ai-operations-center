import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.graph.graph import investigation_graph
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.response import PendingApproval
from app.config import settings

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


# --- Endpoints ---

@router.post("", response_model=StartInvestigationResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_investigation(
    body: StartInvestigationRequest,
    db: AsyncSession = Depends(get_db),
) -> StartInvestigationResponse:
    """
    Trigger a new investigation for an incoming incident alert.
    Returns immediately with an investigation_id; the graph runs asynchronously.
    """
    investigation_id = str(uuid.uuid4())
    thread_config = {"configurable": {"thread_id": investigation_id}}

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
        "synthesis": None,
        "validation_result": None,
        "dispatched_actions": [],
        "pending_approvals": [],
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }

    # TODO: run in background task / Cloud Tasks to avoid blocking the HTTP response
    await investigation_graph.ainvoke(initial_state, config=thread_config)

    return StartInvestigationResponse(
        investigation_id=investigation_id,
        status="accepted",
        message="Investigation started",
    )


@router.get("/{investigation_id}/status", response_model=InvestigationStatusResponse)
async def get_investigation_status(
    investigation_id: str,
    db: AsyncSession = Depends(get_db),
) -> InvestigationStatusResponse:
    """Return the current phase and budget usage for an investigation."""
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await investigation_graph.aget_state(config=thread_config)

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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return synthesis output for a completed investigation."""
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await investigation_graph.aget_state(config=thread_config)

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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the chronological event timeline for an investigation."""
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await investigation_graph.aget_state(config=thread_config)

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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Submit a human approval decision for an L3-escalated investigation.
    On approval, resumes the graph from its saved checkpoint.
    On rejection, marks the investigation as escalated with a reason.
    """
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await investigation_graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    state = snapshot.values

    if state["phase"] != "escalated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Investigation is in phase '{state['phase']}', not awaiting approval",
        )

    if not body.approved:
        # Update state to record the rejection; investigation remains escalated
        await investigation_graph.aupdate_state(
            config=thread_config,
            values={"escalation_reason": f"Rejected by {body.approved_by}: {body.notes}"},
        )
        return {"status": "rejected", "investigation_id": investigation_id}

    # Approval granted - resume from checkpoint
    # The response node will pick up pending_approvals and dispatch
    await investigation_graph.aupdate_state(
        config=thread_config,
        values={"phase": "responding"},
    )
    await investigation_graph.ainvoke(None, config=thread_config)

    return {"status": "approved", "investigation_id": investigation_id}
