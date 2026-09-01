import asyncio
import logging
from datetime import datetime, timezone

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langgraph.types import interrupt
from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.graph.state import InvestigationState
from app.graph.tracing import traced_node
from app.shared.schemas.core import TimelineEvent
from app.shared.schemas.response import DispatchedAction

logger = logging.getLogger(__name__)

_CATEGORY_TO_INCIDENT_TYPE = {
    "deployment":     "failed_deployment",
    "configuration":  "failed_deployment",
    "resource":       "resource_leak",
    "dependency":     "latency_regression",
}


async def _embed_text(text_to_embed: str) -> list[float] | None:
    try:
        embedder = GoogleGenerativeAIEmbeddings(
            model=settings.embedding_model,
        )
        return await embedder.aembed_query(text_to_embed)
    except Exception as exc:
        logger.warning("Embedding call failed, incident_memory stored without vector: %s", exc)
        return None


async def _get_service_id(service_name: str) -> str | None:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT service_id::text FROM services WHERE name = :name"),
            {"name": service_name},
        )
        result = row.fetchone()
        return result[0] if result else None


async def _create_pending_approval(investigation_id: str, action_description: str) -> None:
    """
    Durable record of an L3 approval request. Runs once, from dispatcher_node,
    before the graph ever pauses — l3_approval_gate_node (where interrupt() lives)
    never re-executes this, so this doesn't need to be idempotent against replay.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO pending_approvals
                    (investigation_id, action_description, authority_level, checkpoint_id)
                VALUES (:investigation_id ::uuid, :action_description, 'L3', :investigation_id)
            """),
            {"investigation_id": investigation_id, "action_description": action_description},
        )
        await db.commit()


async def _resolve_pending_approval(
    investigation_id: str,
    approved: bool,
    approved_by: str | None,
    notes: str | None,
    resolved_at: datetime,
) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                UPDATE pending_approvals
                SET approved = :approved, approved_by = :approved_by,
                    approved_at = :resolved_at, notes = :notes
                WHERE investigation_id = :investigation_id ::uuid AND approved IS NULL
            """),
            {
                "investigation_id": investigation_id,
                "approved": approved,
                "approved_by": approved_by,
                "resolved_at": resolved_at,
                "notes": notes,
            },
        )
        await db.commit()


async def _write_incident_memory(investigation_id: str, state: InvestigationState) -> None:
    """
    Persist a completed investigation as an incident_memory record so the Knowledge
    Agent can retrieve similar past incidents. Best-effort — never raises.
    """
    synthesis = state["synthesis"]
    incident = state["incident"]
    budget = state["budget"]
    started_at = state["started_at"]
    completed_at = datetime.now(timezone.utc)
    top = synthesis.top_hypothesis  # type: ignore[union-attr]

    duration_secs = max(0, int((completed_at - started_at).total_seconds()))
    incident_type = _CATEGORY_TO_INCIDENT_TYPE.get(top.root_cause_category, "other")

    embedding_text = (
        f"Incident: {incident.alert_name} affecting {incident.service_name}. "
        f"Severity: {incident.severity}. "
        f"Root cause: {top.description}. "
        f"Category: {top.root_cause_category}. "
        f"Remediation: {top.recommended_action}."
    )

    embedding, service_id = await asyncio.gather(
        _embed_text(embedding_text),
        _get_service_id(incident.service_name),
    )

    onset_dt = (
        incident.onset_timestamp
        if isinstance(incident.onset_timestamp, datetime)
        else datetime.fromisoformat(incident.onset_timestamp.replace("Z", "+00:00"))
    )

    params: dict = {
        "investigation_id": investigation_id,
        "incident_type": incident_type,
        "service_id": service_id,
        # asyncpg binds ::timestamptz params by Python type, not by string parsing —
        # it requires an actual datetime, not the raw ISO string IncidentTrigger carries.
        "onset": onset_dt,
        "resolution": completed_at,
        "category": top.root_cause_category,
        "description": top.description,
        "confidence": top.confidence_pct / 100.0,
        "remediation": top.recommended_action,
        "duration_secs": duration_secs,
        "tool_calls": budget.tool_calls_used,
        "embedding_text": embedding_text,
    }

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE investigations
                    SET phase = 'complete', completed_at = :completed_at
                    WHERE investigation_id = :id ::uuid
                """),
                {"id": investigation_id, "completed_at": completed_at},
            )

            if embedding is not None:
                vec_literal = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"
                sql = f"""
                    INSERT INTO incident_memory (
                        investigation_id, incident_type, affected_service_id,
                        onset_timestamp, resolution_timestamp,
                        root_cause_category, root_cause_description,
                        confidence_at_resolution, remediation_applied,
                        investigation_duration_secs, tool_invocation_count,
                        embedding_text, embedding
                    ) VALUES (
                        :investigation_id ::uuid, :incident_type, :service_id ::uuid,
                        :onset ::timestamptz, :resolution,
                        :category, :description,
                        :confidence, :remediation,
                        :duration_secs, :tool_calls,
                        :embedding_text, '{vec_literal}'::vector
                    )
                """
            else:
                sql = """
                    INSERT INTO incident_memory (
                        investigation_id, incident_type, affected_service_id,
                        onset_timestamp, resolution_timestamp,
                        root_cause_category, root_cause_description,
                        confidence_at_resolution, remediation_applied,
                        investigation_duration_secs, tool_invocation_count,
                        embedding_text
                    ) VALUES (
                        :investigation_id ::uuid, :incident_type, :service_id ::uuid,
                        :onset ::timestamptz, :resolution,
                        :category, :description,
                        :confidence, :remediation,
                        :duration_secs, :tool_calls,
                        :embedding_text
                    )
                """

            await db.execute(text(sql), params)
            await db.commit()
    except Exception as exc:
        logger.error(
            "Failed to write incident_memory for %s: %s", investigation_id, exc, exc_info=True
        )


@traced_node("dispatcher")
async def dispatcher_node(state: InvestigationState) -> dict:
    """
    Executes approved remediation actions and notifies stakeholders.
    Pure workflow — no reasoning: POST Slack record, write incident_memory, update status.
    L1/L2: dispatches immediately and writes incident_memory — terminal, routes to END.
    L3: durably records the approval request and sets phase="escalated" — routes to
    l3_approval_gate (see route_from_dispatcher) rather than pausing itself, so this
    node runs exactly once and never needs to tolerate re-execution.
    """
    synthesis = state["synthesis"]
    top = synthesis.top_hypothesis  # type: ignore[union-attr]
    investigation_id = state["investigation_id"]
    now = datetime.now(timezone.utc)

    if top.authority_level == "L3":
        await _create_pending_approval(investigation_id, top.recommended_action)
        event = TimelineEvent(
            timestamp=now,
            event_type="action_dispatched",
            service=top.affected_service,
            description=f"L3 approval required: {top.recommended_action[:120]}",
            source="action_dispatcher",
        )
        return {
            "timeline": [event],
            "phase": "escalated",
        }

    action = DispatchedAction(
        action_type="slack_message",
        external_id=f"slack-{investigation_id[:8]}",
        dispatched_at=now,
        authority_level=top.authority_level,  # type: ignore[arg-type]
    )
    await _write_incident_memory(investigation_id, state)
    event = TimelineEvent(
        timestamp=now,
        event_type="action_dispatched",
        service=top.affected_service,
        description=(
            f"Investigation complete. Action ({top.authority_level}): "
            f"{top.recommended_action[:120]}"
        ),
        source="action_dispatcher",
    )

    return {
        "dispatched_actions": [action],
        "timeline": [event],
        "phase": "complete",
        "completed_at": now,
    }


@traced_node("l3_approval_gate")
async def l3_approval_gate_node(state: InvestigationState) -> dict:
    """
    Pauses the graph durably for human approval of an L3 action.

    interrupt() raises internally and unwinds the call stack; the checkpointer
    persists the pause (snapshot.next == ("l3_approval_gate",)) and the process is
    free to exit — no long-running container waiting on input. POST /approval
    resumes with graph.ainvoke(Command(resume={...}), config), which re-enters this
    exact node with the human's decision as interrupt()'s return value. Nothing
    before the interrupt() call here, so there's no re-execution-on-resume concern.
    """
    synthesis = state["synthesis"]
    top = synthesis.top_hypothesis  # type: ignore[union-attr]
    investigation_id = state["investigation_id"]

    decision = interrupt(
        {
            "type": "l3_approval_required",
            "investigation_id": investigation_id,
            "action_description": top.recommended_action,
        }
    )

    now = datetime.now(timezone.utc)
    approved = bool(decision.get("approved"))
    approved_by = decision.get("approved_by")
    notes = decision.get("notes")

    await _resolve_pending_approval(investigation_id, approved, approved_by, notes, now)

    if not approved:
        event = TimelineEvent(
            timestamp=now,
            event_type="action_dispatched",
            service=top.affected_service,
            description=(
                f"L3 action rejected by {approved_by or 'unknown'}"
                + (f": {notes}" if notes else "")
            ),
            source="action_dispatcher",
        )
        return {
            "timeline": [event],
            "phase": "escalated",
        }

    action = DispatchedAction(
        action_type="slack_message",
        external_id=f"slack-{investigation_id[:8]}",
        dispatched_at=now,
        authority_level="L3",
    )
    await _write_incident_memory(investigation_id, state)
    event = TimelineEvent(
        timestamp=now,
        event_type="action_dispatched",
        service=top.affected_service,
        description=f"L3 action approved by {approved_by}. {top.recommended_action[:120]}",
        source="action_dispatcher",
    )

    return {
        "dispatched_actions": [action],
        "timeline": [event],
        "phase": "complete",
        "completed_at": now,
    }
