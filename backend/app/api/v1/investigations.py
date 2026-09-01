import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, patch

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import AsyncSessionLocal, get_db
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.response import HypothesisFeedback, PendingApproval
from eval.runner import (
    _build_mock_fetch_deployments,
    _build_mock_fetch_metrics,
    _build_mock_fetch_ownership,
    _build_mock_fetch_topology,
    _build_mock_search_documents,
    _parse_dt,
)

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "eval" / "fixtures"

# Mirrors the budget the offline eval harness (eval/runner.py) uses, which is the
# configuration the committed eval_results/baseline.json was proven against — looser
# confidence threshold and more tool-call headroom than the live-investigation default.
_REPLAY_MAX_ITERATIONS = 7
_REPLAY_MAX_TOOL_CALLS = 28
_REPLAY_CONFIDENCE_THRESHOLD = 0.80

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
    incident: IncidentTrigger
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


class InvestigationListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: str
    incident_id: str
    alert_name: str
    service_name: str
    severity: str
    phase: str
    started_at: datetime
    completed_at: datetime | None
    source: Literal["live", "replay"]
    fixture_id: str | None = None


class HypothesisFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["accepted", "rejected", "challenged"]
    note: str | None = None
    submitted_by: str


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
                    :incident_id ::uuid, :alert_name, :severity, :service_name,
                    :onset_timestamp ::timestamptz, :description, :alert_metadata ::jsonb
                ) ON CONFLICT (incident_id) DO NOTHING
            """),
            {
                "incident_id": incident_db_id,
                "alert_name": incident.alert_name,
                "severity": incident.severity,
                "service_name": incident.service_name,
                # asyncpg binds ::timestamptz params by Python type, not by string
                # parsing — it requires an actual datetime, not the raw ISO string
                # IncidentTrigger carries.
                "onset_timestamp": _parse_dt(incident.onset_timestamp),
                "description": incident.description,
                "alert_metadata": json.dumps(metadata),
            },
        )
        await db.execute(
            text("""
                INSERT INTO investigations (investigation_id, incident_id, phase)
                VALUES (:investigation_id ::uuid, :incident_id ::uuid, 'planning')
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
                    WHERE investigation_id = :id ::uuid
                """),
                {"id": investigation_id},
            )
            await db.commit()


async def _run_replay_investigation(
    graph,
    initial_state: dict,
    thread_config: dict,
    investigation_id: str,
    fixture: dict,
) -> None:
    """
    Background task for fixture replay: identical to _run_investigation except
    external I/O (Cloud Monitoring, deployment DB, knowledge DB, incident-memory
    write) is patched to serve the fixture's pre-authored data, reusing the same
    mock builders the offline eval harness (eval/runner.py) uses. Runs through the
    real graph and the real AsyncPostgresSaver checkpointer, so it is pollable via
    every /investigations/{id}/... endpoint exactly like a live investigation.

    NOTE: unittest.mock.patch here is process-global. Two replay investigations
    running concurrently could in principle cross-contaminate each other's mocked
    I/O. Acceptable for a single-operator demo tool; not engineered around.
    """
    incident: IncidentTrigger = initial_state["incident"]
    onset_dt = _parse_dt(fixture["incident"]["onset_timestamp"])

    mock_metrics = _build_mock_fetch_metrics(fixture.get("telemetry_fixture", {}))
    mock_deployments = _build_mock_fetch_deployments(fixture.get("deployment_fixture", {}), onset_dt)
    mock_docs = _build_mock_search_documents(fixture.get("knowledge_fixture", {}))
    mock_ownership = _build_mock_fetch_ownership(fixture.get("knowledge_fixture", {}))
    mock_topology = _build_mock_fetch_topology(fixture.get("topology_fixture", {}), incident.service_name)
    mock_write_memory = AsyncMock(return_value=None)

    try:
        with (
            patch("app.graph.nodes.telemetry._fetch_cloud_monitoring_metrics", mock_metrics),
            patch("app.graph.nodes.deployment.fetch_deployments", mock_deployments),
            patch("app.graph.nodes.knowledge._search_documents", mock_docs),
            patch("app.graph.nodes.knowledge._fetch_service_ownership", mock_ownership),
            patch("app.graph.nodes.planner._fetch_topology", mock_topology),
            patch("app.graph.nodes.dispatcher._write_incident_memory", mock_write_memory),
        ):
            await graph.ainvoke(initial_state, config=thread_config)
    except Exception as exc:
        logger.error("Replay investigation %s failed unexpectedly: %s", investigation_id, exc, exc_info=True)
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE investigations
                    SET phase = 'escalated', completed_at = NOW()
                    WHERE investigation_id = :id ::uuid
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
        "human_feedback": [],
        "token_log": [],
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
        incident=state["incident"],
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


@router.get("", response_model=list[InvestigationListItem])
async def list_investigations(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[InvestigationListItem]:
    """
    Incident Library history: past investigations (live and replayed), most recent
    first. The `investigations.phase` column is only written at creation and on
    unhandled-error escalation — the checkpointer is the source of truth for live
    phase — so this reads each row's live phase from its checkpoint, falling back to
    the DB column if no checkpoint is found.
    """
    rows = (await db.execute(text("""
        SELECT i.investigation_id::text, i.incident_id::text, i.started_at, i.completed_at,
               i.phase AS db_phase,
               c.alert_name, c.service_name, c.severity, c.alert_metadata
        FROM investigations i
        JOIN incidents c ON c.incident_id = i.incident_id
        ORDER BY i.started_at DESC
        LIMIT 100
    """))).mappings().all()

    graph = request.app.state.investigation_graph
    items: list[InvestigationListItem] = []
    for row in rows:
        thread_config = {"configurable": {"thread_id": row["investigation_id"]}}
        snapshot = await graph.aget_state(config=thread_config)
        phase = snapshot.values["phase"] if snapshot and snapshot.values else row["db_phase"]

        metadata = row["alert_metadata"] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}

        items.append(InvestigationListItem(
            investigation_id=row["investigation_id"],
            incident_id=row["incident_id"],
            alert_name=row["alert_name"],
            service_name=row["service_name"],
            severity=row["severity"],
            phase=phase,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            source="replay" if metadata.get("source") == "replay" else "live",
            fixture_id=metadata.get("replay_fixture_id"),
        ))
    return items


@router.get("/{investigation_id}/evidence")
async def get_evidence(investigation_id: str, request: Request) -> dict:
    """Raw specialist findings gathered so far: telemetry, deployment, knowledge, topology."""
    graph = request.app.state.investigation_graph
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    state = snapshot.values
    topology = state.get("service_topology")

    return {
        "investigation_id": investigation_id,
        "telemetry_findings": [f.model_dump() for f in state.get("telemetry_findings", [])],
        "deployment_findings": [f.model_dump() for f in state.get("deployment_findings", [])],
        "knowledge_context": [k.model_dump() for k in state.get("knowledge_context", [])],
        "service_topology": topology.model_dump() if topology else None,
    }


@router.get("/{investigation_id}/analysis")
async def get_analysis(investigation_id: str, request: Request) -> dict:
    """Pre-safety-guard hypotheses, validation result, per-node token/cost usage, budget."""
    graph = request.app.state.investigation_graph
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    state = snapshot.values
    analysis = state.get("analysis_output")
    validation = state.get("validation_result")

    return {
        "investigation_id": investigation_id,
        "analysis_output": analysis.model_dump() if analysis else None,
        "validation_result": validation.model_dump() if validation else None,
        "token_log": [t.model_dump() for t in state.get("token_log", [])],
        "budget": state["budget"].model_dump(),
        "human_feedback": [h.model_dump() for h in state.get("human_feedback", [])],
        "pending_approvals": [p.model_dump() for p in state.get("pending_approvals", [])],
        "dispatched_actions": [a.model_dump() for a in state.get("dispatched_actions", [])],
    }


@router.post("/replay/{fixture_id}", response_model=StartInvestigationResponse, status_code=status.HTTP_202_ACCEPTED)
async def replay_fixture(fixture_id: str, request: Request) -> StartInvestigationResponse:
    """
    Start an investigation against a pre-authored eval fixture (backend/eval/fixtures/),
    running through the real graph and the real Postgres checkpointer exactly like a
    live investigation — only the external I/O (Cloud Monitoring, deployment DB,
    knowledge DB) is swapped for the fixture's data, reusing eval/runner.py's existing
    mock builders instead of duplicating them.

    Exists because deployment/knowledge evidence comes from real seeded Postgres but
    telemetry comes from live Cloud Monitoring, which has no data for this synthetic
    service — fixtures are the only way to get a full, evidence-rich demo
    investigation through the real API without inventing data.
    """
    fixture_path = FIXTURES_DIR / f"{fixture_id}.json"
    if not fixture_path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown fixture '{fixture_id}'")

    with open(fixture_path) as f:
        fixture = json.load(f)

    incident = IncidentTrigger(**fixture["incident"])
    incident = incident.model_copy(update={
        "alert_metadata": {
            **incident.alert_metadata,
            "source": "replay",
            "replay_fixture_id": fixture_id,
        },
    })

    graph = request.app.state.investigation_graph
    investigation_id = str(uuid.uuid4())
    incident_db_id = str(uuid.uuid4())
    thread_config = {"configurable": {"thread_id": investigation_id}}

    await _persist_incident_and_investigation(incident_db_id, investigation_id, incident)

    initial_state = {
        "investigation_id": investigation_id,
        "incident": incident,
        "phase": "planning",
        "planner_decision": None,
        "planner_working_hypothesis": None,
        "planner_working_confidence": 0.0,
        "budget": InvestigationBudget(
            max_iterations=_REPLAY_MAX_ITERATIONS,
            max_tool_calls=_REPLAY_MAX_TOOL_CALLS,
            max_tokens=settings.max_tokens,
            confidence_threshold=_REPLAY_CONFIDENCE_THRESHOLD,
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
        "human_feedback": [],
        "token_log": [],
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }

    asyncio.create_task(
        _run_replay_investigation(graph, initial_state, thread_config, investigation_id, fixture)
    )

    return StartInvestigationResponse(
        investigation_id=investigation_id,
        status="accepted",
        message=f"Replaying fixture '{fixture_id}'. Poll /status for progress.",
    )


@router.post("/{investigation_id}/hypotheses/{hypothesis_id}/feedback", status_code=status.HTTP_200_OK)
async def submit_hypothesis_feedback(
    investigation_id: str,
    hypothesis_id: str,
    body: HypothesisFeedbackRequest,
    request: Request,
) -> dict:
    """
    Record a human judgment on a specific hypothesis.

    accepted/rejected: pure signal capture for usability-test analysis — appended to
    state.human_feedback, no behavior change.
    challenged: same append, plus resumes the graph from its checkpoint — same
    checkpoint-resume mechanism POST /approval already uses (aupdate_state + a fresh
    background graph.ainvoke(None, ...)) — so the planner picks the investigation
    back up with the human's note visible on its next iteration.
    """
    graph = request.app.state.investigation_graph
    thread_config = {"configurable": {"thread_id": investigation_id}}
    snapshot = await graph.aget_state(config=thread_config)

    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Investigation not found")

    feedback = HypothesisFeedback(
        hypothesis_id=hypothesis_id,
        verdict=body.verdict,
        note=body.note,
        submitted_by=body.submitted_by,
        submitted_at=datetime.now(timezone.utc),
    )
    await graph.aupdate_state(config=thread_config, values={"human_feedback": [feedback]})

    if body.verdict != "challenged":
        return {"status": "recorded", "investigation_id": investigation_id, "verdict": body.verdict}

    phase = snapshot.values["phase"]
    if phase not in ("complete", "escalated"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot challenge while investigation is in phase '{phase}' — "
                   "wait until it completes or escalates",
        )

    await graph.aupdate_state(config=thread_config, values={"phase": "planning"})
    asyncio.create_task(
        _run_investigation(graph, None, thread_config, investigation_id)
    )
    return {"status": "reopened", "investigation_id": investigation_id}
