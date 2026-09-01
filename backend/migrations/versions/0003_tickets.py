"""tickets table - mocked ticketing destination

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-31

Phase 2: a mocked ticketing integration. POST /v1/tickets writes a real row
here and returns it, so it behaves like a real destination for demo/eval
purposes, without calling out to an actual ticketing system (Jira, etc).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tickets (
            ticket_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            title             VARCHAR(500) NOT NULL,
            description       TEXT,
            severity          VARCHAR(5) NOT NULL CHECK (severity IN ('P1', 'P2', 'P3')),
            investigation_id  UUID NOT NULL REFERENCES investigations(investigation_id),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("CREATE INDEX idx_tickets_investigation ON tickets (investigation_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tickets CASCADE;")
