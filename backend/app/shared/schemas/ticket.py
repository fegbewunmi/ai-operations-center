from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    description: str | None = None
    severity: Literal["P1", "P2", "P3"]
    investigation_id: str


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str
    title: str
    description: str | None
    severity: Literal["P1", "P2", "P3"]
    investigation_id: str
    created_at: datetime
