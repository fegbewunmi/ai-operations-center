"""
Integration tests for POST /v1/tickets (app/api/v1/tickets.py).

Requires a live database (see conftest.py) - the endpoint enforces a real
foreign key against investigations, so even the invalid-severity case needs a
running app wired to a real DB session (though it never issues a query, since
TicketCreate's Literal type rejects the request before the handler runs).

Uses httpx.AsyncClient + ASGITransport rather than fastapi.testclient.TestClient:
TestClient runs the ASGI app in its own background thread with its own event
loop, which conflicts with app.db.session's shared async engine when a test
also awaits an async DB fixture (investigation_factory) on pytest-asyncio's
per-test loop - two different event loops touching one asyncpg connection pool
raises "Event loop is closed". Keeping everything on a single event loop (as
test_deployment_node.py's pure-async style already does) avoids that.

That's not sufficient on its own, though: app.db.session.engine is a
module-level singleton, and pytest-asyncio gives each test function its own
fresh event loop by default. A connection pooled during one test still
references that test's (now-closed) loop, so the next test can crash trying to
reuse it. The `client` fixture disposes the pool after every test so each one
starts clean.

Run with:
    DATABASE_URL="postgresql+asyncpg://ai_ops_user:PASSWORD@localhost:5433/ai_ops" \
        pytest tests/test_tickets_api.py -v
"""
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.v1.tickets import router as tickets_router
from app.db.session import engine
from tests.conftest import integration

app = FastAPI()
app.include_router(tickets_router)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


@integration
@pytest.mark.asyncio
async def test_create_ticket_happy_path(client, investigation_factory):
    """A ticket referencing a real investigation is created and returned in full."""
    investigation_id = await investigation_factory()

    resp = await client.post("/v1/tickets", json={
        "title": "Payments error rate spike - v2.3.1 rollback needed",
        "description": "Root cause: v2.3.1 Stripe SDK upgrade.",
        "severity": "P1",
        "investigation_id": investigation_id,
    })

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Payments error rate spike - v2.3.1 rollback needed"
    assert body["description"] == "Root cause: v2.3.1 Stripe SDK upgrade."
    assert body["severity"] == "P1"
    assert body["investigation_id"] == investigation_id
    assert body["ticket_id"]
    assert body["created_at"]


@integration
@pytest.mark.asyncio
async def test_create_ticket_unknown_investigation_404s(client):
    """A well-formed but nonexistent investigation_id must 404, not surface a raw FK violation."""
    resp = await client.post("/v1/tickets", json={
        "title": "test",
        "severity": "P2",
        "investigation_id": "00000000-0000-0000-0000-000000000000",
    })

    assert resp.status_code == 404
    assert "No investigation found" in resp.json()["detail"]


@integration
@pytest.mark.asyncio
async def test_create_ticket_invalid_severity_rejected(client):
    """
    An out-of-range severity is rejected before it ever reaches the DB's
    severity CHECK constraint - TicketCreate.severity is a
    Literal["P1", "P2", "P3"], so FastAPI 422s at the schema layer first.
    """
    resp = await client.post("/v1/tickets", json={
        "title": "test",
        "severity": "P4",
        "investigation_id": "00000000-0000-0000-0000-000000000000",
    })

    assert resp.status_code == 422
