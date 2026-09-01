"""
Shared test fixtures.

Integration tests (marked with @pytest.mark.integration) require a live database
reachable via DATABASE_URL. Run the Cloud SQL Auth Proxy before executing them:

    cloud-sql-proxy ai-ops-center-eb26:us-central1:ai-ops-db --port 5433

    DATABASE_URL="postgresql+asyncpg://ai_ops_user:PASSWORD@localhost:5433/ai_ops" \
        pytest -m integration
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db.session import AsyncSessionLocal

# Skip all integration tests if DATABASE_URL is not set.
db_available = bool(os.getenv("DATABASE_URL"))
integration = pytest.mark.skipif(
    not db_available,
    reason="DATABASE_URL not set - start Cloud SQL Auth Proxy and set DATABASE_URL to run integration tests",
)

# Fixed Orion Commerce service UUIDs from seed migration 0002.
ORDERS_SERVICE_ID = "00000000-0000-0000-0000-000000000002"


@pytest_asyncio.fixture
async def db():
    """Yields an async database session. Each test gets its own session."""
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def deployment_factory(db):
    """
    Factory fixture that inserts deployment records and cleans them up after the test.
    Usage: await deployment_factory(service_id=..., deployed_at=..., status=...)
    Returns a list of created deployment_ids for assertions.
    """
    created_ids: list[str] = []

    async def _create(
        service_id: str = ORDERS_SERVICE_ID,
        version_to: str = "v2.0.0",
        version_from: str = "v1.9.0",
        deployed_at: datetime = datetime.now(timezone.utc),
        deployed_by: str = "test-deployer",
        status: str = "success",
        config_changes: list[str] | None = None,
        rollback_available: bool = True,
        git_commit_sha: str | None = None,
    ) -> str:
        dep_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO deployments (
                    deployment_id, service_id, version_from, version_to,
                    deployed_at, deployed_by, status, config_changes,
                    rollback_available, git_commit_sha
                ) VALUES (
                    :dep_id ::uuid, :service_id ::uuid, :version_from, :version_to,
                    :deployed_at, :deployed_by, :status, :config_changes ::jsonb,
                    :rollback_available, :git_commit_sha
                )
            """),
            {
                "dep_id": dep_id,
                "service_id": service_id,
                "version_from": version_from,
                "version_to": version_to,
                "deployed_at": deployed_at,
                "deployed_by": deployed_by,
                "status": status,
                "config_changes": str(config_changes or []).replace("'", '"'),
                "rollback_available": rollback_available,
                "git_commit_sha": git_commit_sha,
            },
        )
        await db.commit()
        created_ids.append(dep_id)
        return dep_id

    yield _create

    # Teardown - remove test deployments
    for dep_id in created_ids:
        await db.execute(
            text("DELETE FROM deployments WHERE deployment_id = :id ::uuid"),
            {"id": dep_id},
        )
    await db.commit()


@pytest_asyncio.fixture
async def investigation_factory(db):
    """
    Factory fixture that inserts a real incidents + investigations row pair -
    needed to satisfy foreign keys like tickets.investigation_id - and cleans
    both up afterward, along with any tickets created against them.
    Usage: investigation_id = await investigation_factory()
    """
    created: list[tuple[str, str]] = []

    async def _create(phase: str = "complete") -> str:
        incident_id = str(uuid.uuid4())
        investigation_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO incidents (
                    incident_id, alert_name, severity, service_name,
                    onset_timestamp, description
                ) VALUES (
                    :incident_id ::uuid, 'TestAlert', 'P1', 'payments',
                    NOW(), 'test incident'
                )
            """),
            {"incident_id": incident_id},
        )
        await db.execute(
            text("""
                INSERT INTO investigations (investigation_id, incident_id, phase)
                VALUES (:investigation_id ::uuid, :incident_id ::uuid, :phase)
            """),
            {"investigation_id": investigation_id, "incident_id": incident_id, "phase": phase},
        )
        await db.commit()
        created.append((incident_id, investigation_id))
        return investigation_id

    yield _create

    for incident_id, investigation_id in created:
        await db.execute(
            text("DELETE FROM tickets WHERE investigation_id = :id ::uuid"),
            {"id": investigation_id},
        )
        await db.execute(
            text("DELETE FROM investigations WHERE investigation_id = :id ::uuid"),
            {"id": investigation_id},
        )
        await db.execute(
            text("DELETE FROM incidents WHERE incident_id = :id ::uuid"),
            {"id": incident_id},
        )
    await db.commit()
