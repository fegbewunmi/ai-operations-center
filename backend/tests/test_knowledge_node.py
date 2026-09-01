"""
Unit tests for the Knowledge Agent.

Mocks: embedding HTTP call and AsyncSessionLocal DB queries.
No network, no database required.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.knowledge import knowledge_node
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.planner import KnowledgeQuery, PlannerDecision


def _make_state(query: str = "payment service deployment rollback runbook") -> dict:
    incident = IncidentTrigger(
        incident_id="INC-001",
        alert_name="HighErrorRate",
        severity="P1",
        service_name="payments",
        onset_timestamp="2026-08-06T06:00:00Z",
        description="Error rate spiked",
    )
    decision = PlannerDecision(
        action="invoke",
        agent="knowledge",
        query=KnowledgeQuery(
            query=query,
            service_name="payments",
            document_types=["runbook", "postmortem"],
        ),
        reason="Find relevant runbooks",
    )
    return {
        "investigation_id": "test-inv-001",
        "incident": incident,
        "phase": "investigating",
        "planner_decision": decision,
        "planner_working_hypothesis": None,
        "planner_working_confidence": 0.0,
        "budget": InvestigationBudget(),
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
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }


def _mock_db_rows(rows: list[dict]):
    """Build a mock async db session that returns the given rows from execute().mappings().all()."""
    mock_mappings = MagicMock()
    mock_mappings.all.return_value = rows
    mock_result = MagicMock()
    mock_result.mappings.return_value = mock_mappings
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session_maker = MagicMock(return_value=mock_session)
    return mock_session_maker


@patch("app.graph.nodes.knowledge.ChatGoogleGenerativeAI")
@patch("app.graph.nodes.knowledge._embed_query", new_callable=AsyncMock)
@patch("app.graph.nodes.knowledge.AsyncSessionLocal")
@pytest.mark.asyncio
async def test_appends_knowledge_context_with_results(mock_db, mock_embed, mock_llm_class):
    mock_embed.return_value = [0.1] * 768  # fake 768-dim embedding

    doc_rows = [
        {
            "document_id": "doc-001",
            "document_type": "runbook",
            "title": "Payment Service Rollback Runbook",
            "content": "To roll back the payment service: run deploy rollback-payments",
            "distance": 0.1,
        }
    ]
    ownership_rows = [
        {
            "name": "payments",
            "team_name": "Payments Team",
            "slack_channel": "#payments-oncall",
            "pagerduty_rotation": None,
            "runbook_url": None,
            "oncall_contact": None,
        }
    ]

    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        result = MagicMock()
        if call_count == 0:
            # first call: document search
            mappings = MagicMock()
            mappings.all.return_value = doc_rows
            result.mappings.return_value = mappings
        else:
            # second call: ownership lookup
            mappings = MagicMock()
            mappings.fetchone.return_value = ownership_rows[0]
            result.mappings.return_value = mappings
        call_count += 1
        return result

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=side_effect)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db.return_value = mock_session

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "The rollback runbook was found. Key step: run deploy rollback-payments."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    result = await knowledge_node(_make_state())

    assert len(result["knowledge_context"]) == 1
    ctx = result["knowledge_context"][0]
    assert ctx.query == "payment service deployment rollback runbook"
    assert len(ctx.results) == 1
    assert ctx.results[0].title == "Payment Service Rollback Runbook"
    assert "rollback" in ctx.summary.lower()
    assert result["phase"] == "planning"
    assert len(result["timeline"]) == 1


@patch("app.graph.nodes.knowledge.ChatGoogleGenerativeAI")
@patch("app.graph.nodes.knowledge._embed_query", new_callable=AsyncMock)
@patch("app.graph.nodes.knowledge.AsyncSessionLocal")
@pytest.mark.asyncio
async def test_falls_back_to_keyword_search_when_embedding_fails(mock_db, mock_embed, mock_llm_class):
    """When embedding returns None, the node must use keyword search instead."""
    mock_embed.return_value = None  # embedding failure

    mock_session = MagicMock()
    mock_result = MagicMock()
    mappings = MagicMock()
    mappings.all.return_value = []
    mappings.fetchone.return_value = None
    mock_result.mappings.return_value = mappings
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db.return_value = mock_session

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "No documents found."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    # Should not raise even with no embedding
    result = await knowledge_node(_make_state())

    assert len(result["knowledge_context"]) == 1
    assert result["phase"] == "planning"


@patch("app.graph.nodes.knowledge.AsyncSessionLocal")
@pytest.mark.asyncio
async def test_escalates_on_db_failure(mock_db):
    """If the DB query raises, the node should return an error and keep phase=planning."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB connection lost"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db.return_value = mock_session

    with patch("app.graph.nodes.knowledge._embed_query", AsyncMock(return_value=[0.1] * 768)):
        result = await knowledge_node(_make_state())

    assert result["phase"] == "planning"
    assert len(result["error_log"]) == 1
    assert result["error_log"][0].agent == "knowledge"
