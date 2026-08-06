"""seed Orion Commerce service catalog and create vector indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-06

Loads the synthetic Orion Commerce environment:
- 6 services with fixed UUIDs (reproducible across envs)
- Dependency topology
- Team ownership records

Also creates the IVFFlat vector indexes after the tables exist.
The indexes are created here (not in 0001) because IVFFlat performs
better when it has training data available at index creation time.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed UUIDs for the 6 Orion Commerce services.
# Trailing digit identifies the service; all-zeros prefix keeps them readable.
GATEWAY     = "00000000-0000-0000-0000-000000000001"
ORDERS      = "00000000-0000-0000-0000-000000000002"
PAYMENTS    = "00000000-0000-0000-0000-000000000003"
INVENTORY   = "00000000-0000-0000-0000-000000000004"
NOTIFS      = "00000000-0000-0000-0000-000000000005"
USER_AUTH   = "00000000-0000-0000-0000-000000000006"


def upgrade() -> None:
    # ── Services ────────────────────────────────────────────────────────────────
    op.execute(f"""
        INSERT INTO services (service_id, name, description) VALUES
            ('{GATEWAY}',   'api-gateway',   'External entry point; routes all traffic'),
            ('{ORDERS}',    'orders',        'Order lifecycle management'),
            ('{PAYMENTS}',  'payments',      'Payment processing; calls external provider'),
            ('{INVENTORY}', 'inventory',     'Stock tracking; eventually consistent with Orders'),
            ('{NOTIFS}',    'notifications', 'Email/SMS dispatch; non-critical path'),
            ('{USER_AUTH}', 'user-auth',     'Authentication and user profiles; cross-cutting');
    """)

    # ── Topology (upstream -> downstream) ───────────────────────────────────────
    op.execute(f"""
        INSERT INTO service_dependencies (upstream_service_id, downstream_service_id, dependency_type) VALUES
            ('{GATEWAY}',  '{ORDERS}',    'synchronous'),   -- gateway -> orders
            ('{GATEWAY}',  '{USER_AUTH}', 'synchronous'),   -- gateway -> user-auth
            ('{ORDERS}',   '{PAYMENTS}',  'synchronous'),   -- orders -> payments
            ('{ORDERS}',   '{INVENTORY}', 'asynchronous'),  -- orders -> inventory
            ('{ORDERS}',   '{NOTIFS}',    'asynchronous');  -- orders -> notifications
    """)

    # ── Ownership ────────────────────────────────────────────────────────────────
    op.execute(f"""
        INSERT INTO service_ownership (service_id, team_name, slack_channel, pagerduty_rotation, runbook_url) VALUES
            ('{GATEWAY}',   'Platform', '#platform-oncall', 'platform-rotation', 'runbooks/api-gateway.md'),
            ('{ORDERS}',    'Commerce', '#commerce-oncall', 'commerce-rotation', 'runbooks/orders.md'),
            ('{PAYMENTS}',  'Payments', '#payments-oncall', 'payments-rotation', 'runbooks/payments.md'),
            ('{INVENTORY}', 'Commerce', '#commerce-oncall', 'commerce-rotation', 'runbooks/inventory.md'),
            ('{NOTIFS}',    'Platform', '#platform-oncall', NULL,                'runbooks/notifications.md'),
            ('{USER_AUTH}', 'Security', '#security-oncall', 'security-rotation', 'runbooks/user-auth.md');
    """)

    # ── Vector indexes ──────────────────────────────────────────────────────────
    # Created after seed data exists. documents is empty at this point but the
    # table structure is stable. lists=10 is appropriate for < 1,000 vectors;
    # rebuild with higher lists value as corpus grows.
    op.execute("""
        CREATE INDEX idx_documents_embedding ON documents
            USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
    """)

    op.execute("""
        CREATE INDEX idx_incident_memory_embedding ON incident_memory
            USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_incident_memory_embedding;")
    op.execute("DROP INDEX IF EXISTS idx_documents_embedding;")
    op.execute("DELETE FROM service_ownership;")
    op.execute("DELETE FROM service_dependencies;")
    op.execute("DELETE FROM services;")
