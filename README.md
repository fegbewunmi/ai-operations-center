# AI Operations Center

A multi-agent incident investigation system that autonomously gathers evidence, reasons across telemetry and knowledge sources, and recommends remediation - triggered by a single API call from any alerting pipeline.

Built on Google Cloud Platform.

---

## What it does

When a Prometheus or Cloud Monitoring alert fires, a single `POST /v1/investigations` returns **202 immediately** and runs the investigation in the background. A Planner agent (Gemini 2.5 Flash via Vertex AI) coordinates three specialist agents - Telemetry, Deployment, Knowledge - iterating through evidence until it reaches sufficient confidence or exhausts its budget.

**Demo result:** On a simulated P1 incident for the `payments` service, the system independently identified a deployment 15 minutes before onset, cross-referenced a prior postmortem with a matching failure pattern, and returned a ranked root-cause with 85% confidence and a specific rollback recommendation - in under 2 minutes.

**Customer environment:** Orion Commerce - a synthetic 6-service e-commerce platform: API Gateway, Orders, Payments, Inventory, Notifications, User/Auth.

A Next.js investigation console (`frontend/`) sits on top of the API for operators who'd rather watch an investigation unfold than poll `curl` - see [Frontend](#frontend) below.

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
| LLM | Gemini 2.5 Flash (Vertex AI) | Structured output via Pydantic; `ChatGoogleGenerativeAI` + `GOOGLE_GENAI_USE_VERTEXAI=true` |
| Embeddings | text-embedding-004 (768d) | Vertex AI via `GoogleGenerativeAIEmbeddings`; used for knowledge RAG and incident memory |
| Checkpointing | LangGraph AsyncPostgresSaver | Full state persistence; enables mid-graph human approval resume |
| Database | Cloud SQL Postgres 15 + pgvector | Operational data + vector similarity search |
| Metrics source | google-cloud-monitoring | Cloud Monitoring API queried in the incident window |
| Tracing | OpenTelemetry + Cloud Trace | `@traced_node` on every graph node; per-investigation flame graphs |
| Hosting | Cloud Run | Serverless, scales to zero, VPC connector for Cloud SQL |
| Secrets | Secret Manager | DB credentials - never in env files or images; LLM auth via ADC/service account |
| MCP integration | `mcp` SDK (FastMCP, `mcp<2`) | Exposes investigation tools to Claude Desktop/Code over stdio; see [MCP server](#mcp-server) |
| Frontend | Next.js 16 (App Router) + TypeScript | Client of `/v1/*` only - no business logic duplicated; see [Frontend](#frontend) |
| Investigation graph UI | React Flow + `dagre` | Auto-layout canvas rendering the investigation itself (evidence -> hypotheses), not the LangGraph pipeline - see ADR-014 |
| Charts | Recharts | Metrics time series in the Evidence Explorer |
| Styling | Tailwind CSS v4 | CSS-first `@theme inline` config, dark/neutral ops-console palette |

---

## Local setup

### Prerequisites

- Python 3.11+
- [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/mysql/sql-proxy): `brew install cloud-sql-proxy`
- `gcloud` CLI authenticated: `gcloud auth application-default login`

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
# Set: DATABASE_URL, GCP_PROJECT_ID
# LLM auth uses Application Default Credentials — no API key required
```

### Start Cloud SQL Auth Proxy (separate terminal)

```bash
cloud-sql-proxy ai-ops-center-eb26:us-central1:ai-ops-db --port 5433
```

### Run migrations and seed data

```bash
python -m alembic upgrade head
DB_PASSWORD=<pass> python scripts/seed_data.py
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

### Other endpoints

```bash
# Search the knowledge base directly (runbooks, postmortems, architecture docs, error patterns)
curl -X POST http://localhost:8080/v1/knowledge/search \
  -H "Content-Type: application/json" \
  -d '{"query": "database timeout", "document_types": ["runbook"], "limit": 5}'

# Create a ticket for an investigation (Phase 2 - mocked ticketing destination,
# writes a real row but doesn't call an external system)
curl -X POST http://localhost:8080/v1/tickets \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Payments error rate spike - v2.3.1 rollback needed",
    "description": "Root cause: v2.3.1 Stripe SDK upgrade",
    "severity": "P1",
    "investigation_id": "<investigation_id from above>"
  }'
```

---

## Frontend

`frontend/` is a Next.js 16 investigation console: Incident Library, Investigation Workspace (evidence graph, competing hypotheses, agent activity, human approval controls), Evidence Explorer, and an Evaluation/Observability screen. It's a pure client of the API above - no database access, no duplicated business logic, every screen backed by real `/v1/*` responses. See [ADR-014](docs/decisions/ADR-014-investigation-console-frontend.md) for why the central graph shows the investigation instead of the fixed agent pipeline, why updates are polled instead of streamed, and how fixture replay works end-to-end.

### Run it

```bash
cd frontend
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8080
npm run dev
# http://localhost:3000
```

The backend's `FRONTEND_ORIGIN` setting must match wherever this dev server runs (default `http://localhost:3000`) or CORS preflight will fail. See `frontend/README.md` for structure and layout notes.

### End-to-end: replay a fixture through the UI

With the backend running (Cloud SQL Auth Proxy up, `uvicorn app.main:app --reload --port 8080`) and the frontend running (`npm run dev`, port 3000):

1. Open `http://localhost:3000` - the Incident Library lists the 3 fixture scenarios (`INC-FD-001`, `INC-LR-001`, `INC-RL-001`) plus any past investigation history.
2. Click **Start investigation** on a fixture card. This calls `POST /v1/investigations/replay/{fixture_id}`, which runs the fixture through the real graph and checkpointer (mocked external calls, real LLM reasoning) - and redirects to the Investigation Workspace.
3. Watch the Workspace poll live: evidence nodes appear on the central graph as each specialist reports in, hypotheses appear once `incident_analysis` completes, and the Agent Activity panel on the right shows per-node timing and token cost as the investigation progresses.
4. Click a hypothesis to inspect it, or use Accept / Reject / Challenge - Challenge reopens the investigation with your note, visibly re-invoking the planner.
5. `http://localhost:3000/evaluation` is separate from step 2-4: it reads whatever's already been committed to `backend/eval_results/` by `python -m eval.run_eval` (see below), using exactly what `eval/scorer.py` computed. A fixture replayed through the UI doesn't run the scorer or write a new eval result - it's a live investigation for the Workspace to render, not a scored eval run.

---

## MCP server

`mcp_server/` exposes a subset of the investigation API as MCP tools for Claude Desktop, Claude Code, or any other MCP client - so an LLM can pull investigation status, search the knowledge base, and create tickets directly in conversation, without a human working the REST API by hand.

It's a **thin HTTP wrapper**, not a second path into the database - every tool just calls the FastAPI backend above (see `ADR-013`). The backend must already be running for it to do anything.

**Tools:**

| Tool | Wraps | Notes |
|---|---|---|
| `get_investigation_status` | `GET /v1/investigations/{id}/status` | Phase, working hypothesis, confidence, budget usage |
| `search_documents` | `POST /v1/knowledge/search` | Semantic search over runbooks/postmortems/etc |
| `get_incident_history` | `GET /v1/investigations` | Past investigations, most recent first |
| `create_ticket` | `POST /v1/tickets` | **Write, confirm-gated** - `confirm=False` (default) previews with no side effect; only `confirm=True` actually creates the ticket |

### Run it

```bash
cd mcp_server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # add requirements-dev.txt for the MCP Inspector

python server.py                  # runs over stdio; a client spawns this, you don't run it standalone
```

`BACKEND_URL` env var overrides the default `http://localhost:8080`.

### Try it with the MCP Inspector

```bash
pip install -r requirements-dev.txt
mcp dev server.py
```

Opens a local web UI listing all four tools with a form to call each one and inspect the raw request/response.

### Wire it into Claude Code or Claude Desktop

Claude Code: see the repo-root `.mcp.json`.

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "trace-incidents": {
      "command": "/absolute/path/to/mcp_server/.venv/bin/python",
      "args": ["/absolute/path/to/mcp_server/server.py"]
    }
  }
}
```

Fully quit and reopen Claude Desktop after editing - it spawns MCP servers once at launch, not per-conversation.

---

## Run tests

```bash
# Unit tests - no DB or network needed
pytest tests/ -v --ignore=tests/test_deployment_node.py --ignore=tests/test_tickets_api.py

# Integration tests - requires Cloud SQL Auth Proxy on :5433
DATABASE_URL="postgresql+asyncpg://ai_ops_user:PASSWORD@localhost:5433/ai_ops" \
  pytest tests/test_deployment_node.py tests/test_tickets_api.py -v
```

**46 unit tests** (excluding integration tests that require the Auth Proxy): safety_guard (15), planner (7), synthesizer (6), incident_analysis (5), telemetry (5), knowledge (3), eval/fixtures API (5).

`test_tickets_api.py` needs the Auth Proxy for the same reason `test_deployment_node.py` does: importing `app.db.session` requires `DATABASE_URL`/`GCP_PROJECT_ID` to be set just to construct `Settings()`, even for the one test case (invalid severity) that never issues a query.

---

## Run the evaluation harness

The harness replaces all external APIs (Cloud Monitoring, deployment DB, knowledge DB) with pre-authored fixture data while keeping all LLM calls real. Tests agent reasoning without a database or network.

```bash
# Run all 3 fixture incidents (requires ADC — no API key)
GOOGLE_GENAI_USE_VERTEXAI=true \
GOOGLE_CLOUD_PROJECT=ai-ops-center-eb26 \
GOOGLE_CLOUD_LOCATION=us-central1 \
GCP_PROJECT_ID=ai-ops-center-eb26 \
DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db" \
python -m eval.run_eval

# Run one fixture and write JSON results
GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=ai-ops-center-eb26 \
GOOGLE_CLOUD_LOCATION=us-central1 GCP_PROJECT_ID=ai-ops-center-eb26 \
DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db" \
python -m eval.run_eval --fixture INC-FD-001 --output results.json
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
  --set-secrets DATABASE_URL=db-url:latest \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_LOCATION=us-central1
# LLM auth: grant the Cloud Run service account roles/aiplatform.user (no API key in secrets)
```

**Live:** `https://ai-ops-api-zndywutdxa-uc.a.run.app`

---

## Documentation

| Doc | Contents |
|---|---|
| [Architecture Rationale](docs/ARCHITECTURE-RATIONALE.md) | Why each major decision was made - answers for design reviews |
| [Agent Improvements](docs/AGENT-IMPROVEMENTS.md) | Eval debugging log: planner loop fix, safety guard expansion, lessons learned |
| [Design Doc](docs/DESIGN-DOC.md) | Full system design: agents, state model, evaluation, data model |
| [System Architecture](docs/04-system-architecture.md) | Orchestration pattern, state management, data flow |
| [Agent Architecture](docs/05-agent-architecture.md) | Agent boundaries, responsibilities, graph topology |
| [Data Model](docs/06-data-model.md) | Schemas, contracts, versioning strategy |
| [Evaluation](docs/07-evaluation.md) | Eval harness design, metrics, test dataset |
| [Deployment](docs/08-deployment.md) | GCP deployment walkthrough, Cloud Run, CI/CD |
| [Known Issues](docs/KNOWN-ISSUES.md) | Tracked gaps found during development but out of scope for the change that found them |
| [Frontend README](frontend/README.md) | Investigation console structure, setup, and layout notes |

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
| [ADR-010](docs/decisions/ADR-010-planner-routing-fix.md) | Planner routing bug postmortem: discriminated union, mock mismatch, schema gap |
| [ADR-011](docs/decisions/ADR-011-knowledge-evidence-pipeline.md) | Knowledge evidence pipeline: retrieval gap vs. taxonomy gap debugging methodology |
| [ADR-012](docs/decisions/ADR-012-llm-cost-tracking.md) | Per-node LLM token and cost tracking |
| [ADR-013](docs/decisions/ADR-013-mcp-server-integration.md) | MCP server as a thin HTTP wrapper; confirm-gated writes instead of the (broken) L3 approval pattern |
| [ADR-014](docs/decisions/ADR-014-investigation-console-frontend.md) | Investigation console: graph models the investigation not the pipeline, polling over SSE, fixture replay through the real graph, challenge-resume |

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
| `tickets` | Phase 2 mocked ticketing destination - real row, no external call |
