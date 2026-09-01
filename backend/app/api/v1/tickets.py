"""
Mocked ticketing endpoint.

Phase 2: a mocked destination for investigation follow-up. Writes a real row to
the tickets table and returns it — the same shape a real ticketing integration
(Jira, etc.) would return — without calling out to an actual external system.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.shared.schemas.ticket import Ticket, TicketCreate

router = APIRouter(prefix="/v1/tickets", tags=["tickets"])


@router.post("", response_model=Ticket, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    body: TicketCreate,
    db: AsyncSession = Depends(get_db),
) -> Ticket:
    """Create a ticket for an investigation. investigation_id must reference an existing investigation."""
    ticket_id = str(uuid.uuid4())

    try:
        row = (await db.execute(
            text("""
                INSERT INTO tickets (ticket_id, title, description, severity, investigation_id)
                VALUES (:id ::uuid, :title, :description, :severity, :investigation_id ::uuid)
                RETURNING ticket_id::text, title, description, severity, investigation_id::text, created_at
            """),
            {
                "id": ticket_id,
                "title": body.title,
                "description": body.description,
                "severity": body.severity,
                "investigation_id": body.investigation_id,
            },
        )).mappings().one()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No investigation found with id '{body.investigation_id}'",
        )

    return Ticket(**row)
