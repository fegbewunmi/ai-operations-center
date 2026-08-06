# AI Operations Center

An engineering operations platform that reduces Mean Time to Resolution (MTTR) by coordinating production telemetry, deployment intelligence, engineering knowledge, and structured investigation workflows into an automated first-pass root-cause analysis.

Built to demonstrate production-grade multi-agent system design on Google Cloud Platform.

---

## What this system does

When a production incident occurs, on-call engineers spend 15–45 minutes manually correlating logs, metrics, deployment history, and internal documentation before forming a root-cause hypothesis. This system automates that correlation loop, delivering a structured, evidence-backed hypothesis in under 5 minutes - so the engineer's time goes to judgment and action, not data gathering.

**Customer environment:** Orion Commerce - a synthetic mid-size e-commerce platform with services: API Gateway, Orders, Payments, Inventory, Notifications, User/Auth.

---

## Architecture layers

```
┌─────────────────────────────────────────────────────┐
│                      Users                          │
│              (FastAPI · Web UI · Alerts)            │
├─────────────────────────────────────────────────────┤
│                   Agent Layer                       │
│  Planner · Telemetry · Deployment · Knowledge       │
│  Synthesizer · Response · Evidence & Safety Guard   │
├─────────────────────────────────────────────────────┤
│                    Tool Layer                       │
│  Cloud Monitoring · Cloud Logging · GitHub          │
│  Jira · Slack · PagerDuty · pgvector                │
│  Service Catalog (Cloud SQL)                        │
├─────────────────────────────────────────────────────┤
│                  Infrastructure                     │
│  Google Cloud Platform - Cloud Run · Cloud SQL      │
│  Vertex AI · Secret Manager · Cloud Storage         │
└─────────────────────────────────────────────────────┘
```

---

## Documentation

| Doc | Contents |
|---|---|
| [Vision](docs/01-vision.md) | Problem framing, success metrics, customer environment |
| [Discovery](docs/02-discovery.md) | Research, comparable systems, prior art |
| [Requirements](docs/03-requirements.md) | Functional and non-functional requirements |
| [System Architecture](docs/04-system-architecture.md) | Orchestration pattern, state management, data flow |
| [Agent Architecture](docs/05-agent-architecture.md) | Agent boundaries, responsibilities, graph topology |
| [Data Model](docs/06-data-model.md) | Schemas, contracts, versioning strategy |
| [Evaluation](docs/07-evaluation.md) | Eval harness design, metrics, test dataset |
| [Deployment](docs/08-deployment.md) | GCP deployment walkthrough, Cloud Run, CI/CD |
| [Security](docs/09-security.md) | Secret Manager, auth, authority levels |

### Architecture Decision Records

| ADR | Decision |
|---|---|
| [ADR-001](docs/decisions/ADR-001-service-catalog-storage.md) | Service topology and ownership stored in Cloud SQL |
| [ADR-002](docs/decisions/ADR-002-planner-iterative-loop.md) | Planner controls investigation as an iterative loop |
| [ADR-003](docs/decisions/ADR-003-safety-guard-routing.md) | Safety Guard routes failures back to Planner |
| [ADR-004](docs/decisions/ADR-004-human-approval-pattern.md) | Level 3 actions use terminate-checkpoint-resume |
| [ADR-005](docs/decisions/ADR-005-telemetry-agent-merge.md) | Monitoring and Logs merged into single Telemetry Agent |
| [ADR-006](docs/decisions/ADR-006-evaluator-split.md) | Evaluator split into Safety Guard + offline eval harness |
| [ADR-007](docs/decisions/ADR-007-engineering-memory-phasing.md) | Engineering memory schema defined in Phase 1, implemented in Phase 2 |
| [ADR-008](docs/decisions/ADR-008-investigation-budget.md) | Investigation controlled by multi-dimensional budget, not iteration count |
| [ADR-009](docs/decisions/ADR-009-observability-stack.md) | GCP-native observability: Cloud Monitoring + Cloud Logging + Cloud Trace via OpenTelemetry |

---

## Tech stack

| Component | Technology | Why |
|---|---|---|
| Agent orchestration | LangGraph | State-machine pattern; explicit graph topology; built-in checkpointing |
| LLM | Gemini via Vertex AI | GCP-native; required for target deployment environment |
| API layer | FastAPI | Consistent with pipeline-monitor-agent and document-qa seed projects |
| Relational data | Cloud SQL (Postgres) | Service catalog, incident records, approval state |
| Vector search | pgvector (on Cloud SQL) | RAG over runbooks/postmortems; Phase 2 incident memory |
| Deployment | Cloud Run | Serverless; scales to zero; suits terminate-checkpoint-resume approval pattern |
| Secrets | Google Secret Manager | GCP-native secrets management |
| Observability | TBD - see [ADR in progress] | Evaluating Grafana+Prometheus vs. Datadog |

---

## Project phases

**Phase 1 - Core investigation loop (current)**
Read-only investigation across three incident families: failed deployment, latency regression, resource leak. Full agent graph, eval harness, synthetic Orion Commerce environment.

**Phase 2 - Engineering memory**
Write completed investigations to Cloud SQL + pgvector. Knowledge Agent retrieves similar past incidents. Eval compares accuracy with/without memory.

**Phase 3 - Action execution**
Level 2 and Level 3 actions with human-approval workflow. Jira, Slack, PagerDuty integration.
