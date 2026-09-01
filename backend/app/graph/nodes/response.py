# Superseded by dispatcher.py — kept to avoid breaking any cached imports.
# Do not add new logic here.
import asyncio
import logging
from datetime import datetime, timezone

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.graph.state import InvestigationState
from app.graph.tracing import traced_node
from app.shared.schemas.core import TimelineEvent
from app.shared.schemas.response import DispatchedAction, PendingApproval

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

    embedding, service_id = await asyncio.gather(  # type: ignore[name-defined]
        _embed_text(embedding_text),
        _get_service_id(incident.service_name),
    )

    params: dict = {
        "investigation_id": investigation_id,
        "incident_type": incident_type,
        "service_id": service_id,
        "onset": incident.onset_timestamp,
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


@traced_node("response")
async def response_node(state: InvestigationState) -> dict:
    """
    Dispatches approved remediation actions and notifies stakeholders.
    L1/L2: logs a Slack notification record and writes incident_memory.
    L3: creates a PendingApproval and keeps the investigation open for human review.
    """
    synthesis = state["synthesis"]
    top = synthesis.top_hypothesis  # type: ignore[union-attr]
    investigation_id = state["investigation_id"]
    now = datetime.now(timezone.utc)

    dispatched: list[DispatchedAction] = []
    pending: list[PendingApproval] = []

    if top.authority_level == "L3":
        approval = PendingApproval(
            approval_id=f"appr-{investigation_id[:8]}",
            investigation_id=investigation_id,
            action_description=top.recommended_action,
            authority_level="L3",
            checkpoint_id=investigation_id,
            requested_at=now,
        )
        pending.append(approval)
        phase = "escalated"
        description = f"L3 approval required: {top.recommended_action[:120]}"
    else:
        action = DispatchedAction(
            action_type="slack_message",
            external_id=f"slack-{investigation_id[:8]}",
            dispatched_at=now,
            authority_level=top.authority_level,  # type: ignore[arg-type]
        )
        dispatched.append(action)
        phase = "complete"
        description = (
            f"Investigation complete. Action ({top.authority_level}): "
            f"{top.recommended_action[:120]}"
        )
        await _write_incident_memory(investigation_id, state)

    event = TimelineEvent(
        timestamp=now,
        event_type="action_dispatched",
        service=top.affected_service,
        description=description,
        source="response_dispatcher",
    )

    return {
        "dispatched_actions": dispatched,
        "pending_approvals": pending,
        "timeline": [event],
        "phase": phase,
        "completed_at": now,
    }
