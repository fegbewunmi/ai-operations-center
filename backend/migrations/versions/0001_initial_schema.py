"""initial schema - all tables and indexes

Revision ID: 0001
Revises:
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        CREATE EXTENSION IF NOT EXISTS "vector";
    """)

    # ── Orion Commerce service catalog ─────────────────────────────────────────

    op.execute("""
        CREATE TABLE services (
            service_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name         VARCHAR(100) NOT NULL UNIQUE,
            description  TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
        CREATE TABLE service_dependencies (
            upstream_service_id   UUID NOT NULL REFERENCES services(service_id),
            downstream_service_id UUID NOT NULL REFERENCES services(service_id),
            dependency_type       VARCHAR(20) NOT NULL DEFAULT 'synchronous'
                                  CHECK (dependency_type IN ('synchronous', 'asynchronous', 'optional')),
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (upstream_service_id, downstream_service_id),
            CHECK (upstream_service_id != downstream_service_id)
        );
    """)

    op.execute("""
        CREATE TABLE service_ownership (
            service_id          UUID PRIMARY KEY REFERENCES services(service_id),
            team_name           VARCHAR(100) NOT NULL,
            slack_channel       VARCHAR(100) NOT NULL,
            pagerduty_rotation  VARCHAR(255),
            runbook_url         VARCHAR(500),
            oncall_contact      VARCHAR(255),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # ── Deployment history ──────────────────────────────────────────────────────

    op.execute("""
        CREATE TABLE deployments (
            deployment_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            service_id              UUID NOT NULL REFERENCES services(service_id),
            version_from            VARCHAR(100),
            version_to              VARCHAR(100) NOT NULL,
            deployed_at             TIMESTAMPTZ NOT NULL,
            deployed_by             VARCHAR(255) NOT NULL,
            status                  VARCHAR(20) NOT NULL
                                    CHECK (status IN ('success', 'failed', 'rolled_back', 'in_progress')),
            config_changes          JSONB,
            rollback_available      BOOLEAN NOT NULL DEFAULT TRUE,
            rollback_target_version VARCHAR(100),
            git_commit_sha          VARCHAR(40),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
        CREATE INDEX idx_deployments_service_time
            ON deployments (service_id, deployed_at DESC);
    """)

    # ── Incident and investigation records ─────────────────────────────────────

    op.execute("""
        CREATE TABLE incidents (
            incident_id       UUID PRIMARY KEY,
            alert_name        VARCHAR(255) NOT NULL,
            severity          VARCHAR(5) NOT NULL CHECK (severity IN ('P1', 'P2', 'P3')),
            service_name      VARCHAR(100) NOT NULL,
            onset_timestamp   TIMESTAMPTZ NOT NULL,
            description       TEXT,
            alert_metadata    JSONB,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("CREATE INDEX idx_incidents_service ON incidents (service_name);")
    op.execute("CREATE INDEX idx_incidents_onset   ON incidents (onset_timestamp DESC);")

    op.execute("""
        CREATE TABLE investigations (
            investigation_id         UUID PRIMARY KEY,
            incident_id              UUID NOT NULL REFERENCES incidents(incident_id),
            phase                    VARCHAR(20) NOT NULL DEFAULT 'created'
                                     CHECK (phase IN (
                                         'created', 'planning', 'investigating',
                                         'synthesizing', 'responding', 'complete', 'escalated'
                                     )),
            started_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at             TIMESTAMPTZ,
            escalation_reason        TEXT,
            investigation_incomplete BOOLEAN NOT NULL DEFAULT FALSE,
            budget_iterations_used   INT NOT NULL DEFAULT 0,
            budget_tool_calls_used   INT NOT NULL DEFAULT 0,
            budget_tokens_used       INT NOT NULL DEFAULT 0,
            budget_elapsed_seconds   FLOAT NOT NULL DEFAULT 0.0,
            synthesis_json           JSONB,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("CREATE INDEX idx_investigations_incident ON investigations (incident_id);")
    op.execute("CREATE INDEX idx_investigations_phase    ON investigations (phase);")

    # ── Human approval queue ────────────────────────────────────────────────────

    op.execute("""
        CREATE TABLE pending_approvals (
            approval_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            investigation_id   UUID NOT NULL REFERENCES investigations(investigation_id),
            action_description TEXT NOT NULL,
            authority_level    VARCHAR(5) NOT NULL DEFAULT 'L3',
            checkpoint_id      VARCHAR(500) NOT NULL,
            requested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            approved           BOOLEAN,
            approved_by        VARCHAR(255),
            approved_at        TIMESTAMPTZ,
            notes              TEXT
        );
    """)

    op.execute("CREATE INDEX idx_approvals_investigation ON pending_approvals (investigation_id);")
    op.execute("""
        CREATE INDEX idx_approvals_pending
            ON pending_approvals (approved)
            WHERE approved IS NULL;
    """)

    # ── Document store (RAG corpus) ────────────────────────────────────────────

    op.execute("""
        CREATE TABLE documents (
            document_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            title         VARCHAR(500) NOT NULL,
            document_type VARCHAR(50) NOT NULL
                          CHECK (document_type IN (
                              'runbook', 'postmortem', 'architecture_doc', 'error_pattern'
                          )),
            service_name  VARCHAR(100),
            content       TEXT NOT NULL,
            embedding     VECTOR(768),
            source_url    VARCHAR(500),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("CREATE INDEX idx_documents_type    ON documents (document_type);")
    op.execute("CREATE INDEX idx_documents_service ON documents (service_name);")
    # IVFFlat index created after seed data is loaded in 0002 to ensure training data exists.

    # ── Phase 2: incident memory ───────────────────────────────────────────────
    # Table created now; write path activated in Phase 2.

    op.execute("""
        CREATE TABLE incident_memory (
            memory_id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            investigation_id             UUID NOT NULL REFERENCES investigations(investigation_id),
            incident_type                VARCHAR(50) NOT NULL,
            affected_service_id          UUID REFERENCES services(service_id),
            onset_timestamp              TIMESTAMPTZ NOT NULL,
            resolution_timestamp         TIMESTAMPTZ,
            root_cause_category          VARCHAR(50) NOT NULL,
            root_cause_description       TEXT NOT NULL,
            confidence_at_resolution     FLOAT NOT NULL,
            remediation_applied          TEXT,
            investigation_duration_secs  INT NOT NULL,
            tool_invocation_count        INT NOT NULL,
            embedding_text               TEXT NOT NULL,
            embedding                    VECTOR(768),
            created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("CREATE INDEX idx_incident_memory_service ON incident_memory (affected_service_id);")
    op.execute("CREATE INDEX idx_incident_memory_type    ON incident_memory (incident_type);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS incident_memory CASCADE;")
    op.execute("DROP TABLE IF EXISTS documents CASCADE;")
    op.execute("DROP TABLE IF EXISTS pending_approvals CASCADE;")
    op.execute("DROP TABLE IF EXISTS investigations CASCADE;")
    op.execute("DROP TABLE IF EXISTS incidents CASCADE;")
    op.execute("DROP TABLE IF EXISTS deployments CASCADE;")
    op.execute("DROP TABLE IF EXISTS service_ownership CASCADE;")
    op.execute("DROP TABLE IF EXISTS service_dependencies CASCADE;")
    op.execute("DROP TABLE IF EXISTS services CASCADE;")
