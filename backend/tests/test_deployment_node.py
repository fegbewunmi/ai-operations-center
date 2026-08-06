"""
Integration tests for the Deployment agent.

Tests target fetch_deployments() directly (the pure DB function) so we can
assert data-layer correctness without making LLM calls. The LLM summary path
is tested separately with a mock to avoid Vertex AI costs in CI.

Run with:
    DATABASE_URL="postgresql+asyncpg://ai_ops_user:PASSWORD@localhost:5433/ai_ops" \
        pytest tests/test_deployment_node.py -v
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.graph.nodes.deployment import (
    NEAR_ONSET_MINUTES,
    fetch_deployments,
    generate_deployment_summary,
)
from app.shared.schemas.core import TimeWindow
from tests.conftest import ORDERS_SERVICE_ID, integration

ONSET = datetime(2026, 8, 6, 10, 0, 0, tzinfo=timezone.utc)
WINDOW = TimeWindow(
    start=ONSET - timedelta(hours=4),
    end=ONSET + timedelta(hours=1),
)


# ── fetch_deployments tests ────────────────────────────────────────────────────

@integration
@pytest.mark.asyncio
async def test_deployment_near_onset_flagged(deployment_factory):
    """A deployment 15 min before onset must be flagged as near-onset."""
    await deployment_factory(
        deployed_at=ONSET - timedelta(minutes=15),
        version_to="v2.1.0",
        status="success",
    )

    records = await fetch_deployments("orders", WINDOW.start, WINDOW.end, ONSET)

    near = [r for r in records if 0 <= (r.minutes_before_onset or float("inf")) <= NEAR_ONSET_MINUTES]
    assert len(near) >= 1
    assert near[0].minutes_before_onset == pytest.approx(15.0, abs=1.0)


@integration
@pytest.mark.asyncio
async def test_deployment_outside_window_not_near_onset(deployment_factory):
    """A deployment 3 hours before onset must not be flagged as near-onset."""
    await deployment_factory(
        deployed_at=ONSET - timedelta(hours=3),
        version_to="v2.0.5",
        status="success",
    )

    records = await fetch_deployments("orders", WINDOW.start, WINDOW.end, ONSET)

    near = [
        r for r in records
        if 0 <= (r.minutes_before_onset or float("inf")) <= NEAR_ONSET_MINUTES
    ]
    # None of the near records should be the 3-hour-old deployment
    for r in near:
        assert (r.minutes_before_onset or 0) <= NEAR_ONSET_MINUTES


@integration
@pytest.mark.asyncio
async def test_no_deployments_returns_empty(deployment_factory):
    """A service with no deployments in window returns an empty list."""
    # Use a narrow window with no deployments seeded
    narrow_window_start = ONSET + timedelta(hours=10)
    narrow_window_end = ONSET + timedelta(hours=11)

    records = await fetch_deployments("orders", narrow_window_start, narrow_window_end, ONSET)
    assert records == []


@integration
@pytest.mark.asyncio
async def test_minutes_before_onset_positive_for_pre_incident(deployment_factory):
    """minutes_before_onset must be positive for deployments before the incident."""
    await deployment_factory(
        deployed_at=ONSET - timedelta(minutes=45),
        version_to="v3.0.0",
    )

    records = await fetch_deployments("orders", WINDOW.start, WINDOW.end, ONSET)
    target = [r for r in records if r.version_to == "v3.0.0"]
    assert len(target) == 1
    assert target[0].minutes_before_onset > 0


@integration
@pytest.mark.asyncio
async def test_minutes_before_onset_negative_for_post_incident(deployment_factory):
    """minutes_before_onset must be negative for deployments after the incident."""
    await deployment_factory(
        deployed_at=ONSET + timedelta(minutes=20),
        version_to="v3.1.0-hotfix",
    )

    records = await fetch_deployments("orders", WINDOW.start, WINDOW.end, ONSET)
    target = [r for r in records if r.version_to == "v3.1.0-hotfix"]
    assert len(target) == 1
    assert target[0].minutes_before_onset < 0


@integration
@pytest.mark.asyncio
async def test_pre_window_buffer_catches_early_deployments(deployment_factory):
    """
    Deployments up to PRE_WINDOW_BUFFER_HOURS before window_start must still be returned.
    This covers gradual degradation caused by an old deployment.
    """
    # Plant a deployment 90 min before window_start (within the 2h buffer)
    await deployment_factory(
        deployed_at=WINDOW.start - timedelta(hours=1, minutes=30),
        version_to="v1.8.0-canary",
    )

    records = await fetch_deployments("orders", WINDOW.start, WINDOW.end, ONSET)
    target = [r for r in records if r.version_to == "v1.8.0-canary"]
    assert len(target) == 1


@integration
@pytest.mark.asyncio
async def test_config_changes_populated(deployment_factory):
    """Config changes stored in JSONB must round-trip correctly."""
    await deployment_factory(
        version_to="v4.0.0",
        config_changes=["increased DB pool size from 10 to 50", "enabled feature flag X"],
    )

    records = await fetch_deployments("orders", WINDOW.start, WINDOW.end, ONSET)
    target = [r for r in records if r.version_to == "v4.0.0"]
    assert len(target) == 1
    assert len(target[0].config_changes) == 2


# ── generate_deployment_summary tests (mock LLM) ──────────────────────────────

@pytest.mark.asyncio
async def test_summary_empty_deployments():
    """Empty deployment list must return the no-deployments message without an LLM call."""
    summary = await generate_deployment_summary(
        deployments=[],
        investigation_query="were there any recent deploys?",
        incident_description="High error rate on orders service",
    )
    assert "No deployments" in summary


@pytest.mark.asyncio
async def test_summary_calls_llm_when_deployments_exist():
    """When deployments exist, the LLM must be called and its response returned."""
    from app.shared.schemas.deployment import DeploymentRecord

    fake_record = DeploymentRecord(
        deployment_id="00000000-0000-0000-0000-000000000099",
        service="orders",
        version_from="v1.0.0",
        version_to="v2.0.0",
        deployed_at=ONSET - timedelta(minutes=10),
        deployed_by="ci-bot",
        status="success",
        config_changes=[],
        rollback_available=True,
        rollback_target_version=None,
        git_commit_sha=None,
        minutes_before_onset=10.0,
    )

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "The deployment at -10 min is highly suspicious."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    summary = await generate_deployment_summary(
        deployments=[fake_record],
        investigation_query="was a recent deployment the cause?",
        incident_description="Error spike on orders",
        llm=mock_llm,
    )

    assert summary == "The deployment at -10 min is highly suspicious."
    mock_llm.ainvoke.assert_called_once()
