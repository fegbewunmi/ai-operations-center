# AI Operations Center

A multi-agent incident investigation system that autonomously gathers evidence, reasons across telemetry and knowledge sources, and recommends remediation - triggered by a single API call from any alerting pipeline.

Built on Google Cloud Platform to demonstrate production-grade multi-agent system design for a Google Cloud FDE III portfolio.

---

## What it does

When a Prometheus or Cloud Monitoring alert fires, a single `POST /v1/investigations` returns **202 immediately** and runs the investigation in the background. A Planner agent (Gemini 2.5 Flash) coordinates three specialist agents - Telemetry, Deployment, Knowledge - iterating through evidence until it reaches sufficient confidence or exhausts its budget.

**Demo result:** On a simulated P1 incident for the `payments` service, the system independently identified a deployment 15 minutes before onset, cross-referenced a prior postmortem with a matching failure pattern, and returned a ranked root-cause with 85% confidence and a specific rollback recommendation - in under 2 minutes.

**Customer environment:** Orion Commerce - a synthetic 6-service e-commerce platform: API Gateway, Orders, Payments, Inventory, Notifications, User/Auth.

---

## Architecture

```
START
  |
planner  <-----------+----------+-----------+
  |                  |          |           |
  +-- invoke ------> telemetry  deployment  knowledge
  |                  (each loops back to planner)
  |
  +-- synthesize --> incident_analysis
                         |
                     synthesizer
                         |
                    safety_guard
                     /         \
              passed /           \ failed (back to planner)
                   /             
             dispatcher ---------> END
```

The investigation graph is a **LangGraph StateGraph** compiled with an `AsyncPostgresSaver` checkpointer. List fields use `operator.add` reducers so evidence accumulates across iterations. Every node is decorated with `@traced_node` for Cloud Trace visibility.

**Separation of concerns:**
- `planner` - orchestrates, decides what to call next
- `incident_analysis` - correlates all evidence into ranked hypotheses
- `synthesizer` - formats hypotheses into human-readable summary
- `safety_guard` - four deterministic checks before any action is dispatched
- `dispatcher` - pure workflow: POST Slack record, write incident_memory, update status

---

## Tech stack

| Component | Technology | Notes |
|---|---|---|
| API layer | FastAPI + uvicorn | Async-first; returns 202 immediately via `asyncio.create_task` |
| Orchestration | LangGraph StateGraph | Typed reducers, checkpointing, conditional routing |
| LLM | Gemini 2.5 Flash (Gemini API) | Structured output via Pydantic; `ChatGoogleGenerativeAI` |
| Embeddings | gemini-embedding-001 (768d) | Same API key; used for knowledge RAG and incident memory |
| Checkpointing | LangGraph AsyncPostgresSaver | Full state persistence; enables mid-graph human approval resume |
| Database | Cloud SQL Postgres 15 + pgvector | Operational data + vector similarity search |
| Metrics source | google-cloud-monitoring | Cloud Monitoring API queried in the incident window |
| Tracing | OpenTelemetry + Cloud Trace | `@traced_node` on every graph node; per-investigation flame graphs |
| Hosting | Cloud Run | Serverless, scales to zero, VPC connector for Cloud SQL |
| Secrets | Secret Manager | DB credentials and API key - never in env files or images |

---

## Local setup

### Prerequisites

- Python 3.11+
- [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/mysql/sql-proxy): `brew install cloud-sql-proxy`
- `gcloud` CLI: `gcloud auth application-default login`
- Gemini API key from [aistudio.google.com](https://aistudio.google.com)

### Install

```bash
git clone https://github.com/fegbewunmi/ai-operations-center.git
cd ai-operations-center/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Set: DATABASE_URL, GCP_PROJECT_ID, GEMINI_API_KEY
```

### Start Cloud SQL Auth Proxy (separate terminal)

```bash
cloud-sql-proxy ai-ops-center-eb26:us-central1:ai-ops-db --port 5433
```

### Run migrations and seed data

```bash
python -m alembic upgrade head
DB_PASSWORD=<pass> GEMINI_API_KEY=<key> python scripts/seed_data.py
```

### Start the API

```bash
uvicorn app.main:app --reload --port 8080
# Interactive docs: http://localhost:8080/docs
```

---

## Trigger an investigation

```bash
curl -X POST http://localhost:8080/v1/investigations \
  -H "Content-Type: application/json" \
  -d '{
    "incident": {
      "incident_id": "INC-001",
      "alert_name": "HighErrorRate",
      "service_name": "payments",
      "severity": "P1",
      "onset_timestamp": "2026-08-06T06:00:00Z",
      "description": "Payment service error rate spiked to 45% at 06:00 UTC"
    }
  }'

# Poll status
curl http://localhost:8080/v1/investigations/INC-001/status

# Get findings once complete
curl http://localhost:8080/v1/investigations/INC-001/findings
```

---

## Run tests

```bash
# Unit tests - no DB or network needed
pytest tests/ -v --ignore=tests/test_deployment_node.py

# Integration tests - requires Cloud SQL Auth Proxy on :5433
DATABASE_URL="postgresql+asyncpg://ai_ops_user:PASSWORD@localhost:5433/ai_ops" \
  pytest tests/test_deployment_node.py -v
```

**41 tests total:** safety_guard (14), planner (7), synthesizer (6), incident_analysis (5), telemetry (5), knowledge (3), deployment (8 integration + 2 unit).

---

## Run the evaluation harness

The harness replaces all external APIs (Cloud Monitoring, deployment DB, knowledge DB) with pre-authored fixture data while keeping all LLM calls real. Tests agent reasoning without a database or network.

```bash
# Run all 3 fixture incidents (requires only GEMINI_API_KEY)
GEMINI_API_KEY=<key> python -m eval.run_eval

# Run one fixture and write JSON results
GEMINI_API_KEY=<key> python -m eval.run_eval --fixture INC-FD-001 --output results.json
```

**Incident fixtures:**

| Fixture | Family | Challenge |
|---|---|---|
| INC-FD-001 | Failed deployment | Payments v2.3.1 deployed 15 min before onset with Stripe SDK upgrade |
| INC-LR-001 | Latency regression | Orders p99 gradual climb; DB index dropped 2 days prior |
| INC-RL-001 | Resource leak | Inventory connection count grows 4h 20m post-deployment |

**Ship thresholds:** root-cause accuracy >= 75%, evidence completeness >= 80%, required specialists >= 90%, MTTFH < 5 min.

---

## Deploy to Cloud Run

```bash
# Build and push image
docker build --platform linux/amd64 \
  -t us-central1-docker.pkg.dev/<project>/ai-ops-images/api:latest .
docker push us-central1-docker.pkg.dev/<project>/ai-ops-images/api:latest

# Deploy
gcloud run deploy ai-ops-api \
  --image us-central1-docker.pkg.dev/<project>/ai-ops-images/api:latest \
  --region us-central1 \
  --add-cloudsql-instances <project>:us-central1:ai-ops-db \
  --set-secrets DATABASE_URL=db-url:latest,GEMINI_API_KEY=gemini-api-key:latest
```

**Live:** `https://ai-ops-api-zndywutdxa-uc.a.run.app`

---

## Documentation

| Doc | Contents |
|---|---|
| [Architecture Rationale](docs/ARCHITECTURE-RATIONALE.md) | Why each major decision was made - answers for design reviews |
| [Design Doc](docs/DESIGN-DOC.md) | Full system design: agents, state model, evaluation, data model |
| [System Architecture](docs/04-system-architecture.md) | Orchestration pattern, state management, data flow |
| [Agent Architecture](docs/05-agent-architecture.md) | Agent boundaries, responsibilities, graph topology |
| [Data Model](docs/06-data-model.md) | Schemas, contracts, versioning strategy |
| [Evaluation](docs/07-evaluation.md) | Eval harness design, metrics, test dataset |
| [Deployment](docs/08-deployment.md) | GCP deployment walkthrough, Cloud Run, CI/CD |

### Architecture Decision Records

| ADR | Decision |
|---|---|
| [ADR-001](docs/decisions/ADR-001-service-catalog-storage.md) | Service topology stored in Cloud SQL, not an agent |
| [ADR-002](docs/decisions/ADR-002-planner-iterative-loop.md) | Planner controls investigation as an iterative loop |
| [ADR-003](docs/decisions/ADR-003-safety-guard-routing.md) | Safety Guard routes failures back to Planner |
| [ADR-004](docs/decisions/ADR-004-human-approval-pattern.md) | L3 actions use terminate-checkpoint-resume |
| [ADR-005](docs/decisions/ADR-005-telemetry-agent-merge.md) | Monitoring and Logs merged into single Telemetry Agent |
| [ADR-006](docs/decisions/ADR-006-evaluator-split.md) | Evaluator split into Safety Guard + offline eval harness |
| [ADR-007](docs/decisions/ADR-007-engineering-memory-phasing.md) | Incident memory schema in Phase 1, retrieval in Phase 2 |
| [ADR-008](docs/decisions/ADR-008-investigation-budget.md) | Multi-dimensional budget: max iterations + max tool calls |
| [ADR-009](docs/decisions/ADR-009-observability-stack.md) | GCP-native: Cloud Monitoring + Cloud Trace via OpenTelemetry |

---

## Data model

Key tables in Cloud SQL Postgres 15:

| Table | Purpose |
|---|---|
| `incidents` | Alert records with severity and onset timestamp |
| `investigations` | Lifecycle state: phase, started_at, completed_at |
| `services` | Orion Commerce service catalog (6 services) |
| `service_dependencies` | Call graph topology for blast radius analysis |
| `service_ownership` | On-call routing: team, Slack channel, PagerDuty rotation |
| `deployments` | Deployment history with config_changes (JSONB) |
| `documents` | Knowledge base: 11 docs across runbooks, postmortems, architecture |
| `incident_memory` | Past investigations with pgvector(768) for RAG retrieval |
| `checkpoints` | LangGraph state (thread_id, checkpoint JSONB) |
