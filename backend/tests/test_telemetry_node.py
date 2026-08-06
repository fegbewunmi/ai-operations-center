"""
Unit tests for the Telemetry Agent.

Mocks: Cloud Monitoring API call and LLM summary call.
No network or GCP credentials required.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.telemetry import telemetry_node
from app.shared.schemas.core import TimeWindow
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.planner import PlannerDecision, TelemetryQuery
from app.shared.schemas.telemetry import MetricPoint


def _make_metric(name: str = "request_count", value: float = 1200.0) -> MetricPoint:
    return MetricPoint(
        name=name,
        value=value,
        unit="1/s",
        timestamp=datetime(2026, 8, 6, 6, 5, tzinfo=timezone.utc),
        is_anomalous=False,
        baseline_value=None,
    )


def _make_state(service_name: str = "payments") -> dict:
    incident = IncidentTrigger(
        incident_id="INC-001",
        alert_name="HighErrorRate",
        severity="P1",
        service_name=service_name,
        onset_timestamp="2026-08-06T06:00:00Z",
        description="Payment service error rate spiked",
    )
    query = TelemetryQuery(
        service_name=service_name,
        time_window=TimeWindow(
            start=datetime(2026, 8, 6, 5, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc),
        ),
        investigation_query="Is error rate elevated?",
        focus=["metrics"],
    )
    decision = PlannerDecision(
        action="invoke",
        agent="telemetry",
        query=query,
        reason="Establish symptom baseline",
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
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "escalation_reason": None,
        "error_log": [],
    }


@patch("app.graph.nodes.telemetry.ChatGoogleGenerativeAI")
@patch("app.graph.nodes.telemetry._fetch_cloud_monitoring_metrics", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_appends_telemetry_findings_with_metrics(mock_fetch, mock_llm_class):
    metrics = [
        _make_metric("request_count", 1200.0),
        _make_metric("request_latencies", 850.0),
    ]
    mock_fetch.return_value = (metrics, None)

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Error rate is elevated at 45%. Latency at p99 is 850ms."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    result = await telemetry_node(_make_state())

    assert len(result["telemetry_findings"]) == 1
    findings = result["telemetry_findings"][0]
    assert findings.service == "payments"
    assert len(findings.key_metrics) == 2
    assert "elevated" in findings.summary
    assert result["phase"] == "planning"
    assert len(result["timeline"]) == 1


@patch("app.graph.nodes.telemetry.ChatGoogleGenerativeAI")
@patch("app.graph.nodes.telemetry._fetch_cloud_monitoring_metrics", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handles_no_metrics_gracefully(mock_fetch, mock_llm_class):
    """When Cloud Monitoring returns no data, the finding should note it clearly."""
    mock_fetch.return_value = ([], None)

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "No Cloud Monitoring data found for this service."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    result = await telemetry_node(_make_state())

    assert len(result["telemetry_findings"]) == 1
    findings = result["telemetry_findings"][0]
    assert len(findings.key_metrics) == 0
    assert result["phase"] == "planning"

    # Timeline description must mention no data
    event_desc = result["timeline"][0].description
    assert "No Cloud Monitoring data" in event_desc or "0 metric" in event_desc


@patch("app.graph.nodes.telemetry.ChatGoogleGenerativeAI")
@patch("app.graph.nodes.telemetry._fetch_cloud_monitoring_metrics", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_includes_fetch_error_in_findings(mock_fetch, mock_llm_class):
    """An error from Cloud Monitoring must be recorded in TelemetryFindings.error."""
    mock_fetch.return_value = ([], "Cloud Monitoring query failed: permission denied")

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Permission denied accessing metrics."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm_class.return_value = mock_llm

    result = await telemetry_node(_make_state())

    findings = result["telemetry_findings"][0]
    assert findings.error is not None
    assert "permission denied" in findings.error.lower()


@patch("app.graph.nodes.telemetry._fetch_cloud_monitoring_metrics", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_escalates_on_unexpected_exception(mock_fetch):
    """An unexpected exception from the metric fetch must add an error_log entry."""
    mock_fetch.side_effect = RuntimeError("asyncio.to_thread failed")

    result = await telemetry_node(_make_state())

    assert result["phase"] == "planning"
    assert len(result["error_log"]) == 1
    assert result["error_log"][0].agent == "telemetry"


@patch("app.graph.nodes.telemetry.ChatGoogleGenerativeAI")
@patch("app.graph.nodes.telemetry._fetch_cloud_monitoring_metrics", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_summary_fallback_when_llm_fails(mock_fetch, mock_llm_class):
    """If the LLM summary call fails, a mechanical fallback summary must be used."""
    mock_fetch.return_value = ([], None)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    mock_llm_class.return_value = mock_llm

    result = await telemetry_node(_make_state())

    # Should not raise; findings should still be present
    assert len(result["telemetry_findings"]) == 1
    assert result["telemetry_findings"][0].summary != ""
