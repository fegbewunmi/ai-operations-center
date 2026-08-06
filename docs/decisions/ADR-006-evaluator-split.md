# ADR-006: Evaluator Split into Safety Guard and Offline Eval Harness

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

The original 8-agent sketch included an "Evaluator" agent. On inspection, this label was being used for two distinct functions with different stakeholders, cadences, and failure modes:

1. **Runtime quality validation** — checking, during a live investigation, that claims are grounded and actions are safe
2. **System-level accuracy measurement** — measuring, in batch, whether the system correctly identifies root causes across a test dataset

These are not the same problem.

---

## Options considered

**Option A: Single Evaluator agent in the live graph**
- Pros: Simple label; only one component to build and explain
- Cons: A live graph node that also runs batch accuracy evaluation makes no sense architecturally. Batch evaluation requires a labeled test dataset, ground-truth root causes, and comparison logic — none of which exist at investigation runtime. An agent trying to do both would do neither well. The name "Evaluator" in an interview without this distinction would suggest the designer did not fully think through what the component does.

**Option B: Two separate components with distinct roles (selected)**
- **Evidence and Safety Guard** — a LangGraph node that runs on every live investigation. Validates evidence grounding, confidence calibration, and authority-level policy enforcement. Outputs `ValidationResult`.
- **Offline Eval Harness** — a separate Python test suite (in `/evaluations/`) that runs against a labeled dataset of synthetic incidents. Measures root-cause accuracy, MTTFH, unsupported claim rate, tool invocation efficiency, cost per investigation. Completely outside the LangGraph graph.
- Pros: Each component does one thing clearly. The Safety Guard is a safety mechanism; the eval harness is a measurement system. Prompts, failure modes, and iteration cadences are completely different.
- Cons: Two codebases to maintain; requires disciplined separation to prevent scope creep.

---

## Decision

The original Evaluator concept is split into two components. They share no code and no infrastructure.

The Evidence and Safety Guard is Agent 7 in the live graph. The offline eval harness lives in `/evaluations/` and is run as a separate process against the labeled incident dataset.

---

## Consequences

- "Evaluator" as a concept is entirely absent from the live graph — this is intentional
- The Safety Guard can be iterated on to improve runtime safety without touching eval logic
- The eval harness can be run on any version of the system without modifying live agent behavior
- The two components serve as a check on each other: if the Safety Guard passes investigations that the eval harness scores poorly, the Safety Guard's validation logic needs improvement
