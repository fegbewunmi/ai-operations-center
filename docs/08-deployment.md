# Deployment

*Status: Not started. GCP deployment walkthrough - to be written as we execute each step, not before.*

---

## Design decisions made

- **Compute:** Cloud Run. Serverless; scales to zero; compatible with terminate-checkpoint-resume approval pattern. See ADR-004.
- **Model access:** Vertex AI, Gemini models. Model selection to be documented here once chosen.
- **Data:** Cloud SQL (Postgres) for structured data and pgvector.
- **Secrets:** Google Secret Manager.

---

## Planned content (filled in as we deploy, step by step)

- GCP project setup and API enablement
- Vertex AI endpoint configuration and model selection
- Cloud SQL instance setup and schema deployment
- Secret Manager setup and secret references
- Cloud Run service configuration
- Container build and push (Artifact Registry)
- CI/CD pipeline (Cloud Build or GitHub Actions)
- Environment promotion (dev → staging → production)
- Observability stack setup (TBD - see open decision below)

---

## Observability

**Decision:** Cloud Monitoring + Cloud Logging + Cloud Trace via OpenTelemetry. See [ADR-009](decisions/ADR-009-observability-stack.md).

**Setup steps (to be filled in as deployed):**

- Enable Cloud Trace API and Cloud Monitoring API on the GCP project
- Install `opentelemetry-sdk`, `opentelemetry-exporter-gcp-trace`, and `opentelemetry-exporter-gcp-monitoring` in the backend service
- Configure the OpenTelemetry tracer provider with the GCP exporter at application startup
- Create custom metric descriptors for the `custom.googleapis.com/ai_ops/*` namespace
- Build the Cloud Monitoring dashboard (Infrastructure · Investigation Execution · LLM Usage · Eval Results)
