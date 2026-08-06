# ADR-001: Use Gemini API directly (not Vertex AI) for initial implementation

**Status:** Superseded — Vertex AI migration completed 2026-08-06
**Date:** 2026-08-06

## Context

The system needs an LLM for:
- Planner agent structured output (PlannerDecision)
- Synthesizer structured output (SynthesisOutput)
- Knowledge agent summarization
- Embeddings for pgvector document search

Two options in the Google ecosystem:

| | Gemini API (Google AI) | Vertex AI |
|---|---|---|
| Auth | API key | Service account / ADC |
| SDK | `langchain-google-genai` | `langchain-google-genai` v2 (unified) |
| Model access | Same Gemini models | Same Gemini models |
| Billing | Per-request | Per-request (same rates) |
| IAM integration | None | Full GCP IAM |
| Audit logging | None | Cloud Audit Logs |
| VPC Service Controls | No | Yes |
| Enterprise SLA | No | Yes |
| Local dev | Simple (one env var) | Requires ADC setup |

## Original Decision

Start with the **Gemini API** via `langchain-google-genai` and a single `GEMINI_API_KEY`.

Rationale:
- Reduces Day 1 setup friction — API key in Secret Manager is simpler than service account IAM wiring
- Identical model access and output quality; no functional difference at this scale
- Structured output (`.with_structured_output(Pydantic model)`) works identically on both
- Migration to Vertex AI before production is required for IAM, audit trails, and enterprise SLA

## Migration Outcome (2026-08-06)

Migration completed as planned. Two lessons from execution:

**Lesson 1: `langchain-google-vertexai` is deprecated.** The originally-planned migration path (`ChatVertexAI` from `langchain-google-vertexai`) was attempted first. The library works but emits deprecation warnings pointing to `langchain-google-genai` v2 as the unified successor. The final implementation uses `ChatGoogleGenerativeAI` from `langchain-google-genai` with `GOOGLE_GENAI_USE_VERTEXAI=true` — this routes to the Vertex AI endpoint via ADC with no package change from the original Developer API integration.

**Lesson 2: Model availability requires explicit provisioning.** `gemini-2.0-flash-001` was not provisioned in the project's Vertex AI catalog. The system uses `gemini-2.5-flash` (stable alias, available in the project's Model Garden without additional access requests). Model name is centralized in `Settings.gemini_model` so future changes are one-line.

## Final State

| Config | Value |
|---|---|
| Package | `langchain-google-genai>=2.0.0` |
| LLM class | `ChatGoogleGenerativeAI` |
| Vertex AI routing | `GOOGLE_GENAI_USE_VERTEXAI=true` env var |
| LLM model | `gemini-2.5-flash` |
| Embedding class | `GoogleGenerativeAIEmbeddings` |
| Embedding model | `text-embedding-004` (768d) |
| Auth | Application Default Credentials (ADC) |
| Secret Manager | `DATABASE_URL` only — no API key |

Cloud Run service account requires `roles/aiplatform.user` for Vertex AI model access.

## Eval results after migration

All 3 fixture incidents pass on `gemini-2.5-flash` via Vertex AI:

```
Root-cause accuracy:      100%
Evidence completeness:    100%
Required specialists:     100%
Avg MTTFH:                77s
Safety Guard trigger rate: 0%
```

MTTFH increased slightly from 48s (Developer API) to 77s (Vertex AI) — consistent with enterprise endpoint routing overhead, not a model reasoning change.
