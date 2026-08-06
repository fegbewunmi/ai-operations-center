# Data Model

*Status: Locked for Phase 1. Phase 2 fields are present as Optional with None defaults.*
*Last updated: 2026-08-06.*

---

## Organization

All schemas live in a shared package imported by every agent. No agent defines its own types for data shared with other agents.

```
backend/
└── shared/
    └── schemas/
        ├── __init__.py
        ├── core.py          # TimeWindow, TimelineEvent, AgentError
        ├── incident.py      # IncidentTrigger, InvestigationBudget
        ├── planner.py       # PlannerDecision, AgentQuery types
        ├── telemetry.py     # TelemetryFindings and sub-types
        ├── deployment.py    # DeploymentFindings and sub-types
        ├── knowledge.py     # KnowledgeContext, ServiceOwnership, ServiceTopology
        ├── synthesis.py     # Hypothesis, SynthesisOutput, IncidentMemoryRecord
        ├── validation.py    # ValidationResult
        ├── response.py      # DispatchedAction, PendingApproval
        └── state.py         # InvestigationState (imports all of the above)
```

All schemas use `model_config = ConfigDict(extra="forbid")` by default — unexpected fields are caught immediately. The single exception is `IncidentTrigger`, which uses `extra="ignore"` because it receives raw alert payloads from external monitoring systems that may include arbitrary metadata.

---

## Core types

```python
# shared/schemas/core.py
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Literal

class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    end: datetime

class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    event_type: Literal[
        "alert",
        "deployment",
        "metric_anomaly",
        "log_event",
        "config_change",
        "investigation_finding",
        "synthesis",
        "approval_requested",
        "approval_granted",
    ]
    service: str
    description: str
    source: str                  # which agent or tool produced this
    evidence_ref: str | None     # opaque reference to raw evidence in findings

class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    query_summary: str           # short description of what was being queried
    error_type: str              # "tool_failure" | "llm_refusal" | "validation_error" | "timeout"
    message: str
    timestamp: datetime
    retries_attempted: int
```

---

## Input: IncidentTrigger

```python
# shared/schemas/incident.py
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Any, Literal

class IncidentTrigger(BaseModel):
    model_config = ConfigDict(extra="ignore")   # raw alert payloads may have unknown fields
    incident_id: str                            # UUID, assigned by FastAPI on receipt
    alert_name: str
    severity: Literal["P1", "P2", "P3"]
    service_name: str
    onset_timestamp: datetime
    description: str
    alert_metadata: dict[str, Any] = Field(default_factory=dict)

class InvestigationBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Limits (configurable per severity level)
    max_iterations: int = 5
    max_tool_calls: int = 20
    max_tokens: int = 50_000
    max_latency_seconds: float = 30.0
    confidence_threshold: float = 0.90

    # Consumed (updated by each Planner iteration)
    iterations_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0

    @property
    def iterations_remaining(self) -> int:
        return self.max_iterations - self.iterations_used

    @property
    def tool_calls_remaining(self) -> int:
        return self.max_tool_calls - self.tool_calls_used

    @property
    def is_exhausted(self) -> bool:
        return self.iterations_remaining <= 0 or self.tool_calls_remaining <= 0
```

---

## Planner schemas

```python
# shared/schemas/planner.py
from pydantic import BaseModel, ConfigDict
from typing import Literal, Union

class TelemetryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str
    time_window: TimeWindow
    investigation_query: str     # natural-language description of what to look for
    focus: list[Literal["metrics", "logs", "traces"]]

class DeploymentQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str
    time_window: TimeWindow
    investigation_query: str

class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    service_name: str | None = None
    document_types: list[Literal[
        "runbook", "postmortem", "architecture_doc", "error_pattern"
    ]] | None = None

AgentQuery = Union[TelemetryQuery, DeploymentQuery, KnowledgeQuery]

class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["invoke", "synthesize", "escalate"]
    agent: Literal["telemetry", "deployment", "knowledge"] | None = None
    query: AgentQuery | None = None
    reason: str                  # logged for eval and debugging; Planner must always populate this
    working_hypothesis: str | None = None   # Planner's informal running hypothesis
    working_confidence: float = 0.0         # 0.0–1.0; compared to budget.confidence_threshold
```

---

## Telemetry Agent output

```python
# shared/schemas/telemetry.py
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Literal

class MetricPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str                    # e.g. "request_error_rate", "db_query_duration_p99"
    value: float
    unit: str                    # e.g. "percent", "milliseconds", "count"
    timestamp: datetime
    is_anomalous: bool
    baseline_value: float | None # baseline for the same metric in the prior period

class LogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    message: str
    service: str
    trace_id: str | None
    count: int = 1               # if log events are deduplicated

class ResourceUtilization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpu_pct: float | None
    memory_pct: float | None
    connection_count: int | None
    fd_count: int | None         # file descriptor count (relevant for leak incidents)
    thread_count: int | None

class TelemetryFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    time_window: TimeWindow
    query: str                          # the investigation_query this responds to
    key_metrics: list[MetricPoint]
    anomalous_metrics: list[MetricPoint]
    log_events: list[LogEvent]
    resource_utilization: ResourceUtilization
    error_rate_change_pct: float | None  # % change from baseline (positive = increase)
    latency_p99_change_pct: float | None
    summary: str                         # LLM-generated interpretation of findings
    error: str | None = None             # populated on tool failure; signals degraded result
```

---

## Deployment Agent output

```python
# shared/schemas/deployment.py
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Literal

class DeploymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str
    service: str
    version_from: str | None
    version_to: str
    deployed_at: datetime
    deployed_by: str
    status: Literal["success", "failed", "rolled_back", "in_progress"]
    config_changes: list[str]    # human-readable summary of what changed
    rollback_available: bool
    rollback_target_version: str | None
    git_commit_sha: str | None
    minutes_before_onset: float | None  # positive = before incident; negative = after

class DeploymentFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    time_window: TimeWindow
    query: str
    deployments: list[DeploymentRecord]
    deployment_near_onset: bool          # was any deployment within NEAR_ONSET_MINUTES of onset?
    nearest_deployment_minutes: float | None  # minutes between nearest deploy and onset
    summary: str
    error: str | None = None
```

The `NEAR_ONSET_MINUTES` threshold (default: 30) is configuration, not a hardcoded constant. Resource-leak incidents may need a much larger window (hours).

---

## Knowledge Agent output

```python
# shared/schemas/knowledge.py
from pydantic import BaseModel, ConfigDict
from typing import Literal

class KnowledgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    document_type: Literal["runbook", "postmortem", "architecture_doc", "error_pattern"]
    title: str
    excerpt: str                 # the most relevant passage, not the full document
    relevance_score: float       # 0.0–1.0, cosine similarity from pgvector

class SimilarIncident(BaseModel):
    """Phase 2 — populated when incident memory is implemented."""
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    incident_type: str
    affected_service: str
    root_cause_description: str
    remediation_applied: str | None
    similarity_score: float
    days_ago: int                # how long ago this incident occurred

class ServiceOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    team: str
    slack_channel: str
    pagerduty_rotation: str | None
    runbook_url: str | None
    oncall_contact: str | None

class ServiceNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    dependencies: list[str]      # services this one calls (downstream)
    dependents: list[str]        # services that call this one (upstream)
    dependency_types: dict[str, Literal["synchronous", "asynchronous", "optional"]]

class ServiceTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focal_service: str
    nodes: list[ServiceNode]
    critical_path: list[str]     # ordered: entry point → focal service
    blast_radius: list[str]      # services degraded if focal service fails

class KnowledgeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    results: list[KnowledgeResult]
    ownership: ServiceOwnership | None
    similar_incidents: list[SimilarIncident] = []  # empty in Phase 1
    summary: str
    error: str | None = None
```

---

## Synthesizer output

```python
# shared/schemas/synthesis.py
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Literal

class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis_id: str           # UUID, assigned by Synthesizer
    description: str
    root_cause_category: Literal[
        "deployment", "dependency", "resource",
        "configuration", "infrastructure", "unknown"
    ]
    affected_service: str
    confidence_pct: float        # 0.0–100.0
    supporting_evidence: list[str]   # citations to actual findings in InvestigationState
    contradicting_evidence: list[str]
    recommended_action: str
    authority_level: Literal["L1", "L2", "L3"]

class SynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    investigation_summary: str
    timeline: list[TimelineEvent]
    hypotheses: list[Hypothesis]      # ranked by confidence_pct descending
    top_hypothesis: Hypothesis
    investigation_incomplete: bool = False  # True if budget was exhausted
    requires_escalation: bool = False
    escalation_reason: str | None = None
    # Phase 2 — schema defined now; populated in Phase 2 implementation
    incident_memory_record: "IncidentMemoryRecord | None" = None

class IncidentMemoryRecord(BaseModel):
    """Defined in Phase 1. Written to Cloud SQL + pgvector in Phase 2."""
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    investigation_id: str
    incident_type: Literal[
        "failed_deployment", "latency_regression", "resource_leak", "other"
    ]
    affected_service: str
    onset_timestamp: datetime
    resolution_timestamp: datetime | None
    root_cause_category: str
    root_cause_description: str
    confidence_at_resolution: float
    remediation_applied: str | None
    investigation_duration_seconds: int
    tool_invocation_count: int
    embedding_text: str          # normalized, stripped text used to generate the embedding
    embedding: list[float] | None = None  # 768 dimensions; populated when stored in Phase 2
```

---

## Safety Guard output

```python
# shared/schemas/validation.py
from pydantic import BaseModel, ConfigDict
from typing import Literal

class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    issues: list[str]
    risk_level: Literal["L1", "L2", "L3"]  # derived from top_hypothesis.authority_level
    unsupported_claims: list[str]    # hypothesis claims not traceable to a finding in state
    confidence_calibration_ok: bool  # is confidence level reasonable given evidence count?
    action_authority_ok: bool        # does recommended action match stated authority level?
    investigation_incomplete: bool   # forwarded from SynthesisOutput
```

---

## Response Agent output

```python
# shared/schemas/response.py
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Literal

class DispatchedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: Literal["jira_ticket", "slack_message", "pagerduty_incident", "pagerduty_update"]
    external_id: str             # ticket ID, message timestamp, PD incident ID
    url: str | None              # link to the created artifact
    dispatched_at: datetime
    authority_level: Literal["L1", "L2"]

class PendingApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str             # UUID
    investigation_id: str
    action_description: str
    authority_level: Literal["L3"]
    checkpoint_id: str           # LangGraph checkpoint to resume from on approval
    requested_at: datetime
    approved: bool | None = None # None = pending; True = approved; False = rejected
    approved_by: str | None = None
    approved_at: datetime | None = None
    notes: str | None = None
```

---

## Cloud SQL schema (DDL)

```sql
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";  -- pgvector

-- ── Orion Commerce service catalog ────────────────────────────────────────────

CREATE TABLE services (
    service_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         VARCHAR(100) NOT NULL UNIQUE,
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE service_dependencies (
    upstream_service_id   UUID NOT NULL REFERENCES services(service_id),
    downstream_service_id UUID NOT NULL REFERENCES services(service_id),
    dependency_type       VARCHAR(20) NOT NULL DEFAULT 'synchronous'
                          CHECK (dependency_type IN ('synchronous', 'asynchronous', 'optional')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (upstream_service_id, downstream_service_id),
    CHECK (upstream_service_id != downstream_service_id)
);

CREATE TABLE service_ownership (
    service_id          UUID PRIMARY KEY REFERENCES services(service_id),
    team_name           VARCHAR(100) NOT NULL,
    slack_channel       VARCHAR(100) NOT NULL,
    pagerduty_rotation  VARCHAR(255),
    runbook_url         VARCHAR(500),
    oncall_contact      VARCHAR(255),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Synthetic deployment history ──────────────────────────────────────────────

CREATE TABLE deployments (
    deployment_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    service_id              UUID NOT NULL REFERENCES services(service_id),
    version_from            VARCHAR(100),
    version_to              VARCHAR(100) NOT NULL,
    deployed_at             TIMESTAMPTZ NOT NULL,
    deployed_by             VARCHAR(255) NOT NULL,
    status                  VARCHAR(20) NOT NULL
                            CHECK (status IN ('success', 'failed', 'rolled_back', 'in_progress')),
    config_changes          JSONB,            -- list[str] of change descriptions
    rollback_available      BOOLEAN NOT NULL DEFAULT TRUE,
    rollback_target_version VARCHAR(100),
    git_commit_sha          VARCHAR(40),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deployments_service_time ON deployments (service_id, deployed_at DESC);

-- ── Incident and investigation records ────────────────────────────────────────

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

CREATE INDEX idx_incidents_service ON incidents (service_name);
CREATE INDEX idx_incidents_onset   ON incidents (onset_timestamp DESC);

CREATE TABLE investigations (
    investigation_id        UUID PRIMARY KEY,
    incident_id             UUID NOT NULL REFERENCES incidents(incident_id),
    phase                   VARCHAR(20) NOT NULL DEFAULT 'created'
                            CHECK (phase IN (
                                'created', 'planning', 'investigating',
                                'synthesizing', 'responding', 'complete', 'escalated'
                            )),
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ,
    escalation_reason       TEXT,
    investigation_incomplete BOOLEAN NOT NULL DEFAULT FALSE,
    budget_iterations_used  INT NOT NULL DEFAULT 0,
    budget_tool_calls_used  INT NOT NULL DEFAULT 0,
    budget_tokens_used      INT NOT NULL DEFAULT 0,
    budget_elapsed_seconds  FLOAT NOT NULL DEFAULT 0.0,
    synthesis_json          JSONB,   -- denormalized SynthesisOutput for fast GET /findings
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_investigations_incident ON investigations (incident_id);
CREATE INDEX idx_investigations_phase    ON investigations (phase);

-- ── Human approval records ────────────────────────────────────────────────────

CREATE TABLE pending_approvals (
    approval_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    investigation_id   UUID NOT NULL REFERENCES investigations(investigation_id),
    action_description TEXT NOT NULL,
    authority_level    VARCHAR(5) NOT NULL DEFAULT 'L3',
    checkpoint_id      VARCHAR(500) NOT NULL,  -- LangGraph checkpoint reference
    requested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved           BOOLEAN,                -- NULL = pending
    approved_by        VARCHAR(255),
    approved_at        TIMESTAMPTZ,
    notes              TEXT
);

CREATE INDEX idx_approvals_investigation ON pending_approvals (investigation_id);
CREATE INDEX idx_approvals_pending       ON pending_approvals (approved)
    WHERE approved IS NULL;

-- ── Document store (RAG corpus) ───────────────────────────────────────────────

CREATE TABLE documents (
    document_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title         VARCHAR(500) NOT NULL,
    document_type VARCHAR(50) NOT NULL
                  CHECK (document_type IN (
                      'runbook', 'postmortem', 'architecture_doc', 'error_pattern'
                  )),
    service_name  VARCHAR(100),     -- NULL for cross-service documents
    content       TEXT NOT NULL,
    embedding     VECTOR(768),      -- Gemini text-embedding-004
    source_url    VARCHAR(500),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_documents_type    ON documents (document_type);
CREATE INDEX idx_documents_service ON documents (service_name);
CREATE INDEX idx_documents_embedding ON documents
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
-- lists=10 appropriate for corpus under ~1,000 documents; increase as corpus grows

-- ── Phase 2: incident memory ──────────────────────────────────────────────────
-- Table defined now; populated in Phase 2 implementation. See ADR-007.

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

CREATE INDEX idx_incident_memory_service ON incident_memory (affected_service_id);
CREATE INDEX idx_incident_memory_type    ON incident_memory (incident_type);
CREATE INDEX idx_incident_memory_embedding ON incident_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
-- Increase lists value as Phase 2 incident count grows beyond ~1,000
```

---

## pgvector design

Two vector collections, both in Cloud SQL (pgvector extension), both using 768-dimensional Gemini `text-embedding-004` embeddings.

| Collection | Table | Purpose | Phase |
|---|---|---|---|
| Documents | `documents` | RAG over runbooks, postmortems, architecture docs | Phase 1 |
| Incident memory | `incident_memory` | Retrieve similar past investigations | Phase 2 |

**Embedding model:** `text-embedding-004` via Vertex AI. Output dimension: 768. This must be consistent across all ingestion and query calls — mixing embedding models on the same collection produces meaningless similarity scores.

**Index type:** IVFFlat with `lists = 10` for small corpora (< 1,000 vectors). IVFFlat requires a training phase (`ANALYZE`) after the first batch of vectors is loaded. Rule of thumb: `lists = sqrt(row_count)`, recalculate and rebuild the index at 10x data growth.

**Similarity metric:** Cosine similarity (`vector_cosine_ops`). Appropriate for text embeddings where magnitude is not meaningful.

**Query pattern (Knowledge Agent):**
```sql
SELECT document_id, title, document_type, excerpt_function(content), 
       1 - (embedding <=> $1::vector) AS relevance_score
FROM documents
WHERE document_type = ANY($2)           -- optional filter by type
  AND (service_name = $3 OR service_name IS NULL)  -- optional service filter
ORDER BY embedding <=> $1::vector
LIMIT 5;
```

---

## Schema versioning strategy

**Three distinct versioning concerns, three distinct strategies:**

### 1. Database schema (Cloud SQL): Alembic

All migrations in `infrastructure/migrations/`. Never modify the database directly — all changes go through a migration script.

Phase 1 includes the `incident_memory` table DDL (above). It exists but is empty until Phase 2 activates the write path. No migration needed at the Phase 1 → Phase 2 boundary for this table.

### 2. Inter-agent schemas (Pydantic): coordinated deploys

The LangGraph graph is a single deployment unit. All agents share the same Python package and are redeployed together. There is no "Agent A is on schema v1 while Agent B is on schema v2." Schema changes require a redeployment of the entire graph.

**Phase 1 → Phase 2 evolution principle:** Phase 2 fields are present in Phase 1 schemas as `Optional[T] = None`. When Phase 2 code activates:
- `SynthesisOutput.incident_memory_record` changes from `None` to a populated `IncidentMemoryRecord`
- `KnowledgeContext.similar_incidents` changes from `[]` to a populated list
- No schema change; no migration; no version bump

This means schema stability is a design constraint: new optional fields are additive; removing or renaming existing fields requires a deprecation cycle (add new field + keep old field with a deprecation warning, remove old field in the next major version).

### 3. External API (FastAPI): versioned paths from day one

All endpoints are prefixed `/v1/` from the initial release. This reserves room for `/v2/` endpoints if Phase 2 introduces breaking response schema changes (e.g., richer `SynthesisOutput` with incident memory results).

```
POST /v1/investigations
GET  /v1/investigations/{id}
GET  /v1/investigations/{id}/findings
GET  /v1/investigations/{id}/timeline
POST /v1/investigations/{id}/approvals/{approval_id}
```

---

## Seed data: Orion Commerce service catalog

The following seed data initialises the service catalog for the synthetic environment. Applied via a seed migration in Alembic.

```sql
-- Services
INSERT INTO services (service_id, name, description) VALUES
    ('00000000-0000-0000-0000-000000000001', 'api-gateway',    'External entry point; routes all traffic'),
    ('00000000-0000-0000-0000-000000000002', 'orders',         'Order lifecycle management'),
    ('00000000-0000-0000-0000-000000000003', 'payments',       'Payment processing; calls external provider'),
    ('00000000-0000-0000-0000-000000000004', 'inventory',      'Stock tracking; eventually consistent with Orders'),
    ('00000000-0000-0000-0000-000000000005', 'notifications',  'Email/SMS dispatch; non-critical path'),
    ('00000000-0000-0000-0000-000000000006', 'user-auth',      'Authentication and user profiles; cross-cutting');

-- Topology (upstream → downstream)
INSERT INTO service_dependencies (upstream_service_id, downstream_service_id, dependency_type) VALUES
    ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', 'synchronous'),  -- gateway → orders
    ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000006', 'synchronous'),  -- gateway → user-auth
    ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000003', 'synchronous'),  -- orders → payments
    ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000004', 'asynchronous'), -- orders → inventory
    ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000005', 'asynchronous'); -- orders → notifications

-- Ownership
INSERT INTO service_ownership (service_id, team_name, slack_channel, pagerduty_rotation, runbook_url) VALUES
    ('00000000-0000-0000-0000-000000000001', 'Platform',  '#platform-oncall',  'platform-rotation',  'runbooks/api-gateway.md'),
    ('00000000-0000-0000-0000-000000000002', 'Commerce',  '#commerce-oncall',  'commerce-rotation',  'runbooks/orders.md'),
    ('00000000-0000-0000-0000-000000000003', 'Payments',  '#payments-oncall',  'payments-rotation',  'runbooks/payments.md'),
    ('00000000-0000-0000-0000-000000000004', 'Commerce',  '#commerce-oncall',  'commerce-rotation',  'runbooks/inventory.md'),
    ('00000000-0000-0000-0000-000000000005', 'Platform',  '#platform-oncall',  NULL,                 'runbooks/notifications.md'),
    ('00000000-0000-0000-0000-000000000006', 'Security',  '#security-oncall',  'security-rotation',  'runbooks/user-auth.md');
```

---

## Judgment calls in this section

Three decisions are embedded here that you should consciously own:

**1. Denormalized `synthesis_json` in the `investigations` table**

`GET /v1/investigations/{id}/findings` returns the full `SynthesisOutput`. Loading the LangGraph checkpoint to get this on every poll is expensive. I store a JSONB snapshot of `SynthesisOutput` in `investigations.synthesis_json`, written when the graph completes. The tradeoff: the same data lives in two places (checkpoint + JSONB column). If the checkpoint and the column diverge (bug in the write step), `GET /findings` would return stale data. This is acceptable at this scale; it would not be acceptable in a system where the synthesis is updated post-completion.

**2. IVFFlat over HNSW for pgvector indexes**

IVFFlat is simpler to configure and query time is fast enough at small corpus sizes (< 10,000 vectors). HNSW offers better query time at scale but uses significantly more memory (approximately 8 bytes × dimension × rows). For the Orion Commerce corpus (15–20 documents in Phase 1, hundreds in Phase 2), IVFFlat is the right starting point. The index type can be changed without a schema migration — drop and recreate.

**3. Fixed-width UUIDs for Orion Commerce seed services**

The seed services use fixed UUIDs (all zeros with a single digit suffix). This makes the seed data reproducible and the SQL readable in debugging, at the cost of looking slightly artificial. An alternative is to use generated UUIDs and reference services by name in foreign keys. The fixed approach is fine for a synthetic environment.
