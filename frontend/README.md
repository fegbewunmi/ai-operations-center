# Trace — Investigation Console

Next.js 16 (App Router) + TypeScript frontend for the AI Operations Center backend. Not a chatbot: an evidence-first operations console — Incident Library, Investigation Workspace, Evidence Explorer, Evaluation/Observability — backed entirely by the real FastAPI + LangGraph backend in `../backend`.

See the [repo root README](../README.md) for the full-system picture and [ADR-014](../docs/decisions/ADR-014-investigation-console-frontend.md) for why this frontend is shaped the way it is.

---

## Prerequisites

- Node.js 20+
- The backend running and reachable (see `../backend/README` / repo root README) — this app has no data or logic of its own, it's a client of `/v1/*`.

## Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local
# NEXT_PUBLIC_API_BASE_URL defaults to http://localhost:8080 — change it if your
# backend runs elsewhere.
```

## Run

```bash
npm run dev
# http://localhost:3000
```

The backend must have `FRONTEND_ORIGIN` set to match wherever this dev server actually runs (default `http://localhost:3000`) or `POST`/`GET` calls will fail CORS preflight.

## Build / lint

```bash
npm run build   # next build — type-checks and produces a production build
npm run lint    # eslint
```

There is no frontend test suite — correctness here is enforced by TypeScript (types in `lib/types.ts` are hand-mapped 1:1 to the backend's Pydantic schemas, no OpenAPI codegen) plus manual verification against the running backend.

## Structure

```
app/
  page.tsx                          Incident Library — launch a fixture, browse history
  investigations/[id]/page.tsx      Investigation Workspace (flagship screen)
  evaluation/page.tsx               Evaluation / Observability
components/
  incident-library/  workspace/  evidence/  evaluation/
lib/
  types.ts                          Hand-mapped backend response/schema types
  api.ts                            Typed fetch client
  useInvestigationPolling.ts        Polling hook (no SSE/WebSockets — see ADR-014)
  deriveEvidenceLinks.ts            Investigation-graph node/edge derivation — the
                                     documented heuristic behind the central graph
```

## The one thing worth understanding before touching `InvestigationGraph.tsx`

The center canvas on the Workspace screen is **not** a picture of the fixed 8-node LangGraph pipeline. It renders the investigation itself — incident → evidence → hypotheses — derived from real state in `lib/deriveEvidenceLinks.ts`. Node/agent execution (which of the 8 backend nodes ran, when, at what cost) lives in the separate `AgentActivityPanel`. Evidence→hypothesis edges are *inferred* by substring-matching real identifiers against hypothesis citation text, not a backend-modeled relationship — read the comment block at the top of `deriveEvidenceLinks.ts` before changing the matching rule, and see ADR-014 for why this is a heuristic rather than a backend field.

## Layout

The Investigation Workspace is viewport-locked, IDE-style: the global header and per-investigation status bar are fixed, and each panel (Evidence rail, graph, Detail, Hypotheses, Agent Activity) scrolls independently. The page itself never grows — see `app/layout.tsx` (`body: h-full overflow-hidden`) and `components/ui.tsx`'s `Panel`. If you add a new panel, give its scrollable content its own `flex flex-col` + `overflow-auto` — a plain `<div className="flex-1 min-h-0">` without `flex` on it will silently break containment (this bit the original build once; see ADR-014).
