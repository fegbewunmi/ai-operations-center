# ADR-011: Knowledge Evidence Pipeline — Retrieval Gap vs. Taxonomy Gap

**Status:** Resolved  
**Date:** August 6, 2026  
**Context:** INC-LR-001 root-cause category failure (expected "configuration", got "resource")

---

## Background

After the planner routing bug was fixed (ADR-010), INC-LR-001 still fails: the system identifies the correct service and calls the correct specialists, but classifies the root cause as "resource" instead of "configuration". Ground truth is "configuration" because a schema migration dropped a database index — a deliberate change to system configuration that produced resource-like symptoms (gradual p99 latency climb, no error rate increase, normal CPU/memory).

---

## The Diagnostic Fork

Before deciding on a fix, two distinct failure modes had to be distinguished:

**Failure mode A — Retrieval gap:** The knowledge agent retrieved the relevant postmortem and runbook, but the causal fact ("dropped index") did not survive the summarization step and never reached `incident_analysis`. The classifier failed because it was missing evidence, not because it misclassified evidence it had.

**Failure mode B — Taxonomy gap:** The causal fact did reach `incident_analysis` via the knowledge summary, but the `root_cause_category` enum had no definitions distinguishing "configuration" (cause = a schema/setting change) from "resource" (cause = exhaustion of a finite resource). The classifier made a reasonable-sounding choice from an ambiguous taxonomy.

This distinction matters for fix ordering: a taxonomy fix applied before retrieval is confirmed addresses the wrong layer. If the summary never contained "dropped index," then even a perfect category definition cannot produce the right answer — the classifier cannot categorize a cause it never received.

---

## Evidence Pipeline Trace

The pipeline from fixture documents to `incident_analysis` input:

```
fixture knowledge_fixture.documents
    → _search_documents mock (eval)     # returns KnowledgeResult with full excerpt
    → _generate_knowledge_summary       # real LLM call: receives excerpt[:200], outputs 2-3 sentence summary
    → KnowledgeContext.summary          # summary string only; raw excerpts stored in KnowledgeContext.results
    → _build_evidence_block             # passes k.summary + doc titles to incident_analysis
                                        # raw excerpts were NOT forwarded (before this ADR's fix)
```

The fixture's postmortem excerpt is 190 characters:
> "Root cause: Migration 0021 dropped an index on orders.customer_id marked as unused. Latency climbed from 180ms to 3400ms over 40 minutes. No error rate increase initially."

This passes fully to `_generate_knowledge_summary`. Based on the prompt ("summarise what the knowledge base reveals... note key remediation steps") and the explicitness of the postmortem's first sentence, the summary very likely contains "dropped index" or equivalent. This makes **failure mode B (taxonomy gap) the more probable root cause** — but this was not confirmed by observing the actual summary at runtime before fixes were applied.

The structural observation is independent of which failure mode applied: `_build_evidence_block` was dropping raw excerpts, passing only the LLM-generated summary to `incident_analysis`. This is a lossy pipeline. The knowledge agent's summary is a 2-3 sentence compression of documents that may contain multiple relevant facts; the classifier at `incident_analysis` has less signal than the evidence actually retrieved.

---

## Fixes Applied

### Fix 1: Category definitions in incident_analysis system prompt

Added explicit definitions for each `root_cause_category` value, including the key distinction between "configuration" and "resource":

```python
# Before: bare list
"Categorise the root cause: deployment | dependency | resource | configuration | infrastructure | unknown"

# After: definitions included
Root cause category definitions — use the most specific fit:
  deployment:      A code release or binary change caused the regression
  dependency:      An upstream or downstream service degraded and propagated here
  resource:        The system exhausted a finite resource (CPU, memory, connections, disk)
                   NOT caused by a schema or configuration change
  configuration:   A schema change (dropped index, altered column, changed constraint),
                   config setting, feature flag, or parameter change caused the regression —
                   the system is correctly resourced but misconfigured or mis-schemaed
  infrastructure:  Underlying infrastructure (network, load balancer, availability zone) failed
  unknown:         Evidence is insufficient to categorize
```

The critical rule encoded: "resource" is for running-out-of-a-finite-resource scenarios; "configuration" covers schema/index/setting changes regardless of whether they produce resource-like symptoms.

This fix addresses failure mode B. If failure mode A is also present (summary didn't contain the causal fact), this fix alone will not resolve the accuracy failure.

### Fix 2: Raw excerpts forwarded to incident_analysis

`_build_evidence_block` now includes up to 300 characters of raw excerpt per knowledge result, in addition to the LLM-generated summary:

```python
# Before
parts.append(f"  {k.summary}")
for r in k.results[:3]:
    parts.append(f"  - [{r.document_type}] {r.title} (relevance: {r.relevance_score:.2f})")

# After
parts.append(f"  {k.summary}")
for r in k.results[:3]:
    parts.append(f"  - [{r.document_type}] {r.title} (relevance: {r.relevance_score:.2f})")
    if r.excerpt:
        parts.append(f"    Excerpt: {r.excerpt[:300]}")
```

This addresses failure mode A by eliminating the lossy summarization bottleneck. `incident_analysis` now receives both the knowledge agent's interpretation (summary) and the raw source text (excerpts). Even if the knowledge summary underweights the causal fact, the raw postmortem excerpt — which starts "Root cause: Migration 0021 dropped an index" — is now directly visible to the classifier.

Fix 2 is correct regardless of which failure mode applies. It does not depend on confirming the retrieval gap hypothesis first.

---

## Validation Result

INC-LR-001 re-run after both fixes:

```
Phase:       complete
Accuracy:    PASS  (category=True, service=True)
Evidence:    PASS  (>=2 supporting items)
Specialists: PASS  called=['deployment', 'knowledge', 'telemetry']
Confidence:  80%
MTTFH:       91s   (was 50s with wrong answer)
Iterations:  6     (was 4)
Safety Guard triggered: True
```

Accuracy is now correct. The safety guard triggered once, which explains the extra 2 iterations and 41 seconds.

### Why the safety guard triggered

The safety guard's `_check_source_alignment` check (line 65 of `safety_guard.py`) requires knowledge to have been called for any "configuration" or "infrastructure" hypothesis:

```python
if category in ("configuration", "infrastructure") and not has_knowledge:
    issues.append("Root cause 'configuration' ... knowledge agent was not consulted")
```

The planner synthesized before calling knowledge on its first pass. With the taxonomy fix in place, the model now correctly classified the root cause as "configuration" early — but knowledge had not yet been called at that point, so source_alignment failed. The guard routed back to the planner with the failure reason, the planner called knowledge, and the second synthesis passed all four checks.

The wrong classification ("resource") had been passing source_alignment silently — resource does not require knowledge evidence. The correct classification ("configuration") exposed the planner's suboptimal ordering. The guard functioned as designed: a second line of defense that caught a synthesis missing required evidence.

### Failure mode resolved

The taxonomy gap was the dominant failure mode for this fixture. The postmortem excerpt (*"Root cause: Migration 0021 dropped an index"*) was available to the knowledge LLM, and the knowledge agent's summary very likely included the index-drop fact — the classifier simply had no rule to map "dropped index" to "configuration" vs "resource." Adding category definitions with the critical rule ("resource = exhausted a finite resource, NOT caused by a schema/config change") resolved the classification.

Fix 2 (raw excerpts forwarded to `incident_analysis`) made the pipeline more robust regardless, ensuring the raw causal fact is visible to the classifier even if the knowledge summary underweights it.

---

## Lesson: Fix Ordering and Gating

The sequence mattered here. The taxonomy fix (Fix 1) was applied first, before confirming that the evidence containing the cause actually reached `incident_analysis`. This created a validation risk: if INC-LR-001 passes after both fixes, we cannot attribute the resolution to Fix 1 alone without inspection.

The correct sequence for future cases of this type:
1. Confirm the evidence trail before attributing a failure to classification
2. Structural fixes (widening the evidence pipeline) should precede classifier prompt changes, because classifier fixes can only be fairly evaluated once the classifier receives the correct input
3. Re-run after each fix independently when possible; joint fixes make attribution ambiguous

The exception: if a structural fix is clearly correct independent of the failure mode (as Fix 2 is here), it can be applied before the diagnostic is complete without risk — it cannot produce a false resolution.

---

## Addendum: Category Definition Regression on INC-RL-001

The first version of the category definitions broke INC-RL-001 (resource leak, inventory). The initial "deployment" definition — *"A code release or binary change caused the regression"* — was too broad: RL-001's deployment at 260 minutes before onset did introduce the connection pool bug, so the model correctly applied that definition and returned "deployment." But the ground truth is "resource."

The distinction the fixture encodes: FD-001 is "deployment" because errors appeared immediately after the release (15 min gap, rollback is the fix). RL-001 is "resource" because the connection pool depleted gradually over 4+ hours — the deployment introduced a bug, but the failure mechanism is a finite pool hitting its limit. Rollback alone doesn't drain leaked connections; the fix involves patching the session management code.

The final category definitions encode this with two additions: (1) "deployment" now requires symptoms within 30 minutes of deploy; (2) "resource" explicitly covers "exhaustion triggered by a deployment bug but building over time." This resolved RL-001 while preserving the LR-001 fix.

## Files Changed

| File | Change |
|---|---|
| `app/graph/nodes/incident_analysis.py` | Added category definitions (with deployment/resource temporal distinction); forwarded raw excerpts in `_build_evidence_block` |
| `app/graph/nodes/planner.py` | Added no-recent-deploy heuristic (>720 min threshold) to push knowledge call before synthesis |
| `eval/scorer.py` | Added `actual_root_cause_category` and `expected_root_cause_category` to `EvalScore` |
| `eval/run_eval.py` | Added category fields to JSON output |
