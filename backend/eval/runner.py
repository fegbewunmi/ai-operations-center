"""
Evaluation harness runner.

Runs the investigation graph against fixture data, replacing all external API
calls (Cloud Monitoring, deployment DB, knowledge DB) with pre-authored fixture
responses while keeping all LLM calls real.

This tests whether agent *reasoning* is correct given specific evidence — not
whether the production API integrations work (that is integration testing).

Usage:
    from eval.runner import run_fixture
    state = await run_fixture(Path("eval/fixtures/INC-FD-001.json"))
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import MemorySaver

from app.graph.graph import build_graph
from app.shared.schemas.deployment import DeploymentRecord
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.knowledge import (
    KnowledgeResult,
    ServiceOwnership,
    ServiceNode,
    ServiceTopology,
)
from app.shared.schemas.telemetry import MetricPoint


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _build_mock_fetch_metrics(telemetry_fixture: dict):
    async def _mock(service_name: str, window_start: datetime, window_end: datetime):
        raw = telemetry_fixture.get(service_name, [])
        metrics = [
            MetricPoint(
                name=m["name"],
                value=m["value"],
                unit=m["unit"],
                timestamp=_parse_dt(m["timestamp"]),
                is_anomalous=m.get("is_anomalous", False),
                baseline_value=m.get("baseline_value"),
            )
            for m in raw
        ]
        return metrics, None

    return _mock


def _build_mock_fetch_deployments(deployment_fixture: dict, onset_dt: datetime):
    async def _mock(service_name: str, window_start: datetime, window_end: datetime, onset_dt_arg: datetime):
        raw = deployment_fixture.get(service_name, [])
        records = []
        for d in raw:
            deployed_at = _parse_dt(d["deployed_at"])
            minutes_before = (onset_dt - deployed_at).total_seconds() / 60.0
            records.append(
                DeploymentRecord(
                    deployment_id=d["deployment_id"],
                    service=d["service"],
                    version_from=d["version_from"],
                    version_to=d["version_to"],
                    deployed_at=deployed_at,
                    deployed_by=d["deployed_by"],
                    status=d["status"],
                    config_changes=d.get("config_changes", []),
                    rollback_available=d.get("rollback_available", False),
                    rollback_target_version=d.get("rollback_target_version"),
                    git_commit_sha=d.get("git_commit_sha"),
                    minutes_before_onset=minutes_before,
                )
            )
        return records

    return _mock


def _build_mock_search_documents(knowledge_fixture: dict):
    async def _mock(query_text: str, service_name=None, doc_types=None, limit: int = 5):
        raw = knowledge_fixture.get("documents", [])
        return [
            KnowledgeResult(
                document_id=d["document_id"],
                document_type=d["document_type"],
                title=d["title"],
                excerpt=d["excerpt"],
                relevance_score=d.get("relevance_score", 0.5),
            )
            for d in raw[:limit]
        ]

    return _mock


def _build_mock_fetch_ownership(knowledge_fixture: dict):
    async def _mock(service_name: str):
        raw = knowledge_fixture.get("ownership")
        if not raw:
            return None
        return ServiceOwnership(
            service=raw["service"],
            team=raw["team"],
            slack_channel=raw["slack_channel"],
            pagerduty_rotation=raw.get("pagerduty_rotation"),
            runbook_url=raw.get("runbook_url"),
            oncall_contact=raw.get("oncall_contact"),
        )

    return _mock


def _build_mock_fetch_topology(topology_fixture: dict, service_name: str):
    async def _mock(svc: str):
        if not topology_fixture:
            return None
        node = ServiceNode(
            service=svc,
            dependencies=topology_fixture.get("dependencies", []),
            dependents=topology_fixture.get("dependents", []),
            dependency_types=topology_fixture.get("dependency_types", {}),
        )
        return ServiceTopology(
            focal_service=svc,
            nodes=[node],
            critical_path=topology_fixture.get("critical_path", []),
            blast_radius=topology_fixture.get("blast_radius", []),
        )

    return _mock


async def run_fixture(fixture_path: Path) -> dict:
    """
    Run the investigation graph against a fixture file.

    Returns the final LangGraph state dict. If the graph ended in escalation
    or failure, the state will reflect that — the runner does not raise.
    """
    with open(fixture_path) as f:
        fixture = json.load(f)

    incident_data = fixture["incident"]
    incident = IncidentTrigger(**incident_data)
    onset_dt = _parse_dt(incident_data["onset_timestamp"])

    telemetry_fx = fixture.get("telemetry_fixture", {})
    deployment_fx = fixture.get("deployment_fixture", {})
    knowledge_fx = fixture.get("knowledge_fixture", {})
    topology_fx = fixture.get("topology_fixture", {})

    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)

    investigation_id = f"eval-{incident_data['incident_id']}"
    thread_config = {"configurable": {"thread_id": investigation_id}}

    initial_state = {
        "investigation_id": investigation_id,
        "incident": incident,
        "phase": "planning",
        "planner_decision": None,
        "planner_working_hypothesis": None,
        "planner_working_confidence": 0.0,
        "budget": InvestigationBudget(
            max_iterations=7,
            max_tool_calls=28,
            confidence_threshold=0.80,
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

    mock_metrics = _build_mock_fetch_metrics(telemetry_fx)
    mock_deployments = _build_mock_fetch_deployments(deployment_fx, onset_dt)
    mock_docs = _build_mock_search_documents(knowledge_fx)
    mock_ownership = _build_mock_fetch_ownership(knowledge_fx)
    mock_topology = _build_mock_fetch_topology(topology_fx, incident.service_name)
    mock_write_memory = AsyncMock(return_value=None)

    with (
        patch("app.graph.nodes.telemetry._fetch_cloud_monitoring_metrics", mock_metrics),
        patch("app.graph.nodes.deployment.fetch_deployments", mock_deployments),
        patch("app.graph.nodes.knowledge._search_documents", mock_docs),
        patch("app.graph.nodes.knowledge._fetch_service_ownership", mock_ownership),
        patch("app.graph.nodes.planner._fetch_topology", mock_topology),
        patch("app.graph.nodes.dispatcher._write_incident_memory", mock_write_memory),
    ):
        final_state = await graph.ainvoke(initial_state, config=thread_config)

    return final_state
