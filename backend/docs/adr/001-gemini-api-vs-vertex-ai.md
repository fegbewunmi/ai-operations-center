# ADR-001: Use Gemini API directly (not Vertex AI) for initial implementation

**Status:** Accepted — migration to Vertex AI planned as a production milestone  
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
| SDK | `langchain-google-genai` | `langchain-google-vertexai` |
| Model access | Same Gemini models | Same Gemini models |
| Billing | Per-request | Per-request (same rates) |
| IAM integration | None | Full GCP IAM |
| Audit logging | None | Cloud Audit Logs |
| VPC Service Controls | No | Yes |
| Enterprise SLA | No | Yes |
| Local dev | Simple (one env var) | Requires ADC setup |

## Decision

Start with the **Gemini API** via `langchain-google-genai` and a single `GEMINI_API_KEY`.

Rationale:
- Reduces Day 1 setup friction — API key in Secret Manager is simpler than service account IAM wiring
- Identical model access and output quality; no functional difference at this scale
- LangChain's `ChatGoogleGenerativeAI` and `ChatVertexAI` share the same interface — migration is a swap, not a rewrite
- Structured output (`.with_structured_output(Pydantic model)`) works identically on both

## Consequences

- Local development requires only `GEMINI_API_KEY` — no `gcloud auth application-default login` needed for the LLM calls
- No Cloud Audit Logs for LLM calls (only relevant for compliance-sensitive environments)
- Migration to Vertex AI before production is required for:
  - GCP-native IAM and audit trails
  - VPC Service Controls (if data classification requires it)
  - Enterprise SLA coverage

## Migration plan (production milestone)

1. Replace `langchain-google-genai` with `langchain-google-vertexai` in `pyproject.toml`
2. Change `ChatGoogleGenerativeAI(google_api_key=...)` → `ChatVertexAI(model=..., project=..., location=...)` in synthesizer, planner, and knowledge nodes
3. Update embedding call in `knowledge.py` to use the Vertex AI Embeddings API instead of the direct REST call
4. Remove `GEMINI_API_KEY` from Secret Manager; ensure Cloud Run service account has `roles/aiplatform.user`
5. Remove `gemini_api_key` from `Settings`; add `gcp_region` (already present) for Vertex AI endpoint selection
6. Update local dev docs: `gcloud auth application-default login` now required
