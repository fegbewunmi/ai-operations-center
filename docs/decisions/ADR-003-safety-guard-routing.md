# ADR-003: Safety Guard Routes Failures Back to Planner, Not Synthesizer

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

The Evidence and Safety Guard runs between the Synthesizer and Response Agent, validating that hypothesis claims are evidence-grounded, confidence is calibrated, and proposed actions are within the appropriate authority level. When validation fails, the system must decide where to route the failure.

---

## Options considered

**Option A: Route back to Synthesizer for revision**
- Pros: The Synthesizer already has all the evidence; a revision pass is cheaper than gathering new evidence
- Cons: If a claim is unsupported, the Synthesizer cannot invent evidence. It can only lower its confidence or remove the claim - which produces a weaker hypothesis but does not fix the underlying evidence gap. Repeatedly revising without new evidence is a loop with no improving signal. The Synthesizer revising its own output without new data is not investigation; it is editing.

**Option B: Route back to Planner (selected)**
- Pros: A failed validation almost always indicates missing evidence, not poor reasoning over complete evidence. The Planner is the only agent that can invoke additional specialists to fill the gap. Routing here keeps the feedback loop honest: the system admits it needs more data, not that it needs to reword what it already said.
- Cons: Routing back to the Planner consumes additional budget (one more Planner iteration + at least one more specialist call). May not be possible if budget is exhausted.

**Option C: Fail and escalate immediately**
- Pros: Simple; no retry complexity
- Cons: Too aggressive for a first-pass failure. A single unsupported claim should not escalate the entire investigation if budget remains and the claim could be grounded with one more tool call.

---

## Decision

The Safety Guard routes validation failures to the Planner, passing the list of unsupported claims or policy violations as context for the next iteration. The Planner decides whether to invoke additional specialists to address the gaps.

**Exception:** If `budget.iterations_remaining == 0`, the Safety Guard escalates rather than routing to the Planner. There is no value in a feedback loop with no budget to act on.

---

## Consequences

- Failed validations drive real additional investigation, not cosmetic hypothesis revision
- Budget consumption on failure is real - the eval harness should track how often the Safety Guard triggers a re-investigation loop
- The Planner must be prompted to use the Safety Guard's `issues` list to decide which specialist to call next
- A Synthesizer that consistently fails validation is a signal that the Planner is under-investigating before handing off, not that the Synthesizer is poorly prompted
