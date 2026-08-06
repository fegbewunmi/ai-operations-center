# Evaluation Design

*Status: Locked for Phase 1. Phase 2 experiment (with/without memory) described but not yet runnable.*
*Last updated: 2026-08-06.*

---

## Design principle

The evaluation harness is designed before agent logic is written. This is not ceremonial - metrics chosen after implementation will be biased toward what the system already does well. Metrics chosen before implementation describe what the system should do, and failing them is information.

The eval harness must answer one question per run: **is this version of the system better or worse than the previous version?** Everything else follows from that.

---

## What the eval harness tests (and what it does not)

**Tests:**
- Whether the Planner invokes the right specialists for a given incident type
- Whether specialist agents correctly interpret fixture data and produce accurate summaries
- Whether the Synthesizer correctly identifies root cause and ranks hypotheses
- Whether the Safety Guard catches unsupported claims
- Whether the full pipeline hits the success metrics from Section 1

**Does not test:**
- Whether the tool implementations correctly call real external APIs (that is integration testing, not eval)
- Whether the system handles network failures gracefully (that is chaos/fault-injection testing)
- Whether prompts are optimally worded (that is prompt engineering, guided by eval results)

This distinction matters: the eval harness calls real Gemini models (via Vertex AI) for every agent LLM call. It does not mock LLMs. It only mocks the external tool calls (Cloud Monitoring, Cloud Logging, deployment history) by injecting fixture data. You are testing whether the agents reason correctly, not whether they can call APIs.

---

## Eval harness architecture

```
evaluations/
├── harness.py                # main runner: loads fixtures, runs graph, scores outputs
├── metrics.py                # metric calculation functions (pure functions, no LLM)
├── judge.py                  # LLM-as-judge utilities (for qualitative metrics)
├── comparison.py             # compare two EvalResult runs (regression detection)
├── fixtures/
│   ├── failed_deployment/
│   │   ├── INC-FD-001/       # obvious: error immediately after deploy
│   │   │   ├── ground_truth.json
│   │   │   ├── monitoring.json     # fixture: what Cloud Monitoring returns
│   │   │   ├── logging.json        # fixture: what Cloud Logging returns
│   │   │   └── deployments.json    # fixture: deployment history
│   │   ├── INC-FD-002/       # delayed: failure 20 minutes post-deploy
│   │   ├── INC-FD-003/       # misleading: deploy happened but is not the cause
│   │   ├── INC-FD-004/       # multi-service: deploy to Payments cascades to Orders
│   │   └── INC-FD-005/       # rollback: system should recommend, not just diagnose
│   ├── latency_regression/
│   │   ├── INC-LR-001/       # single-hop: Orders → slow DB query
│   │   ├── INC-LR-002/       # multi-hop: Orders → Payments → slow external provider
│   │   ├── INC-LR-003/       # misleading metrics: high CPU is a symptom, not the cause
│   │   ├── INC-LR-004/       # gradual: latency degraded over 2 hours, not a step change
│   │   └── INC-LR-005/       # transient: latency spike, already recovered, root cause unknown
│   └── resource_leak/
│       ├── INC-RL-001/       # fast leak: fd exhaustion within 30 minutes
│       ├── INC-RL-002/       # slow leak: connection count growing over 6 hours
│       ├── INC-RL-003/       # misleading logs: timeout errors surface, leak is underlying
│       ├── INC-RL-004/       # partial recovery: restart fixed it, system must identify cause
│       └── INC-RL-005/       # multi-service: Inventory leak causes Notifications backlog
├── results/                  # stored EvalResult JSON per run (gitignored from large runs)
└── reports/                  # generated comparison reports (markdown)
```

**15 incidents in the initial dataset: 5 per incident family, spanning easy/medium/hard difficulty.** This is enough for signal without being expensive to run on every PR. The dataset grows as new incident families are added.

### How fixture injection works

Each specialist agent receives its tools via dependency injection. In production, the tool implementations call real APIs. In eval, they are replaced with fixture loaders:

```python
# Production: calls real Cloud Monitoring
async def query_monitoring(service: str, time_window: TimeWindow, ...) -> dict:
    return await cloud_monitoring_client.query(...)

# Eval: loads fixture file instead
async def query_monitoring_fixture(
    service: str, time_window: TimeWindow, ..., incident_id: str
) -> dict:
    fixture_path = FIXTURES_DIR / incident_id / "monitoring.json"
    return json.loads(fixture_path.read_text())
```

This means the agent's LLM reasoning runs for real. Only the external data source is replaced. The Telemetry Agent receives the same JSON it would get from Cloud Monitoring - but the JSON was written by you, with known content, rather than fetched from a live system.

### Running an eval

```python
from evaluations.harness import EvalHarness

harness = EvalHarness(
    incident_ids=["INC-FD-001", "INC-FD-002", ...],  # or "all"
    use_fixtures=True,
    graph_config=GraphConfig(model="gemini-2.0-flash", ...),
)
result: EvalResult = await harness.run()

# Compare to a stored baseline
from evaluations.comparison import compare_runs
report = compare_runs(baseline=load_result("results/baseline.json"), current=result)
print(report.summary())
```

Each eval run costs real Gemini API tokens. At 15 incidents × ~6 LLM calls per investigation (3–5 Planner iterations + Synthesizer + Safety Guard), expect approximately 90 Gemini calls per full eval run. Plan for this cost when running eval on every PR vs. only before merging.

---

## Ground truth schema

Every incident fixture has a `ground_truth.json` that fully describes the expected outcome:

```python
# evaluations/schemas.py
from pydantic import BaseModel
from typing import Literal

class IncidentGroundTruth(BaseModel):
    incident_id: str
    incident_family: Literal["failed_deployment", "latency_regression", "resource_leak"]
    difficulty: Literal["easy", "medium", "hard"]

    # Root cause (what the system must identify)
    true_root_cause_category: Literal[
        "deployment", "dependency", "resource",
        "configuration", "infrastructure", "unknown"
    ]
    true_affected_service: str
    true_root_cause_description: str   # used by LLM judge for semantic similarity
    true_remediation: str

    # Investigation requirements
    required_specialist_calls: list[Literal["telemetry", "deployment", "knowledge"]]
    # All specialists in this list must be called at least once for the investigation
    # to be considered complete. Not calling a required specialist is a planning failure.

    # Misleading signals (what the system must not get confused by)
    misleading_signals: list[str]

    # Expected output shape
    expected_authority_level: Literal["L1", "L2", "L3"]
    acceptable_escalation: bool = False
    # True only for INC-LR-005 (transient, root cause genuinely uncertain)
```

**Example - INC-FD-001:**
```json
{
  "incident_id": "INC-FD-001",
  "incident_family": "failed_deployment",
  "difficulty": "easy",
  "true_root_cause_category": "deployment",
  "true_affected_service": "payments",
  "true_root_cause_description": "Version 2.4.1 of the Payments service introduced an unhandled NullPointerException in the checkout flow, causing 5xx errors on all payment requests beginning at 09:13 UTC.",
  "true_remediation": "Roll back Payments service to version 2.4.0.",
  "required_specialist_calls": ["deployment", "telemetry"],
  "misleading_signals": [
    "Orders service error rate increased as a downstream effect - Orders is not the root cause",
    "High thread count in Payments logs is a consequence of the exception, not a resource leak"
  ],
  "expected_authority_level": "L2",
  "acceptable_escalation": false
}
```

---

## Metrics

### 1. Root-cause accuracy

**What it measures:** Did the system correctly identify what broke and why?

**Calculation:**
```python
def root_cause_accuracy(synthesis: SynthesisOutput, truth: IncidentGroundTruth) -> dict:
    top = synthesis.top_hypothesis
    return {
        "full_match": (
            top.root_cause_category == truth.true_root_cause_category
            and top.affected_service == truth.true_affected_service
        ),
        "category_correct": top.root_cause_category == truth.true_root_cause_category,
        "service_correct":  top.affected_service == truth.true_affected_service,
    }
```

Report both the strict score (category AND service) and the component scores. Component scores tell you whether the system found the right service but miscategorized the cause (Planner issue) or found the right category but blamed the wrong service (topology reasoning issue).

**Target:** ≥ 75% strict (full_match) across the 15-incident dataset.

**Reported by family:**
- Failed deployment incidents are expected to have the highest accuracy (change-causality is the clearest signal)
- Resource leak incidents are expected to have the lowest (gradual causality, misleading surface signals)

---

### 2. Mean Time to First Hypothesis (MTTFH)

**What it measures:** How fast does the system produce a synthesis output?

**Calculation:**
```python
mttfh_seconds = (synthesis_timestamp - investigation_started_at).total_seconds()
```

**Nuance:** In eval, LLM latency is real (Gemini API calls take 1–5 seconds each). Wall-clock MTTFH in eval includes this. Production MTTFH may differ based on concurrency and model endpoint latency. Report both the median and p95 - a high p95 reveals incidents that send the Planner into a long investigation loop.

**Target:** Median < 5 minutes on the synthetic dataset.

---

### 3. Evidence completeness

**What it measures:** Are hypotheses backed by enough evidence to be actionable?

**Calculation:**
```python
def evidence_completeness(synthesis: SynthesisOutput) -> bool:
    return len(synthesis.top_hypothesis.supporting_evidence) >= 2
```

A hypothesis with one or zero evidence items is a guess, not a finding. An on-call engineer cannot act on it.

**Target:** ≥ 80% of investigations across the dataset.

---

### 4. Unsupported claim rate

**What it measures:** What fraction of evidence citations cannot be traced to actual findings in the investigation state?

**Calculation:**
```python
def unsupported_claim_rate(validation: ValidationResult, synthesis: SynthesisOutput) -> float:
    total_claims = sum(
        len(h.supporting_evidence) for h in synthesis.hypotheses
    )
    if total_claims == 0:
        return 0.0
    return len(validation.unsupported_claims) / total_claims
```

This uses the Safety Guard's `ValidationResult.unsupported_claims` output, which means the Safety Guard must run as part of every eval investigation (even if, in the eval, we do not route failures back to the Planner - we record the failure and continue).

**Target:** < 10% across the dataset.

---

### 5. Required specialist call rate

**What it measures:** Did the Planner invoke all the specialists necessary to reach the correct diagnosis?

**Calculation:**
```python
def required_specialist_coverage(
    actual_calls: list[str],
    truth: IncidentGroundTruth
) -> bool:
    return set(truth.required_specialist_calls).issubset(set(actual_calls))
```

This is recall-focused: we penalize missing a critical specialist far more than calling an extra one. A Planner that skips Deployment for a failed-deployment incident is making a serious investigation error.

**Target:** ≥ 90% of investigations invoke all required specialists.

---

### 6. Tool invocation efficiency

**What it measures:** Is the Planner using its budget wisely, or making redundant calls?

**Calculation:**
```python
redundant_calls = total_specialist_calls - len(set(specialist_calls_with_distinct_queries))
efficiency_score = 1.0 - (redundant_calls / total_specialist_calls)
```

A Planner that calls Telemetry three times with the same query is wasting budget. A Planner that calls Telemetry twice with distinct queries (metrics first, then logs) is using it correctly.

**Target:** Tracked and reported. No hard threshold in Phase 1 - used to tune Planner prompts.

---

### 7. Investigation cost

**What it measures:** Token consumption and estimated cost per investigation.

**Calculation:**
```python
# Collected from LangGraph state budget tracking
tokens_used = state["budget"].tokens_used

# Estimated cost (update as Vertex AI pricing changes)
estimated_cost_usd = tokens_used * GEMINI_COST_PER_TOKEN
```

**Target:** Tracked and reported. Reported by incident family and difficulty. No hard threshold in Phase 1. Used in Phase 2 to measure whether engineering memory reduces cost by shortcutting investigations.

---

### 8. Safety Guard trigger rate

**What it measures:** What fraction of investigations produce a validation failure?

**Calculation:**
```python
trigger_rate = sum(1 for r in results if not r.validation_result.passed) / len(results)
```

A high trigger rate means the Planner is terminating investigations before gathering sufficient evidence, leaving the Synthesizer to make unsupported claims. This metric should trend downward as Planner prompts improve.

**Target:** < 30% in the initial version. < 20% as prompts mature. Treat a rising trigger rate across eval runs as a regression signal.

---

### 9. Confidence calibration

**What it measures:** Is the Synthesizer's stated confidence correlated with actual accuracy?

**Calculation:** After running all 15 incidents, group hypotheses by confidence band and measure accuracy per band:

```python
# Expected: high confidence → high accuracy
bands = {
    "high (≥85%)":   [s for s in scores if s.top_confidence >= 85],
    "medium (70–84%)": [...],
    "low (<70%)":     [...],
}
for band, band_scores in bands.items():
    accuracy = mean(s.root_cause_correct for s in band_scores)
    print(f"{band}: accuracy={accuracy:.0%}")
```

A well-calibrated Synthesizer should show monotonically increasing accuracy as confidence increases. Miscalibration (high confidence + wrong answer) is more dangerous than low confidence + wrong answer - the former misleads the on-call engineer; the latter prompts them to investigate further.

**Target:** Accuracy in the high-confidence band ≥ 85%. No incidents with confidence ≥ 90% and `root_cause_correct = False`.

---

### 10. Escalation accuracy

**What it measures:** When the system escalates (budget exhausted, or unresolvable), is that the right call?

**Calculation:**
```python
def escalation_appropriate(
    escalated: bool,
    truth: IncidentGroundTruth,
) -> bool | None:  # None if not applicable
    if not escalated:
        return None
    return truth.acceptable_escalation
```

Escalating INC-LR-005 (transient, genuinely uncertain) is correct. Escalating INC-FD-001 (obvious deployment failure) is a system failure.

**Target:** 0 inappropriate escalations on easy/medium incidents.

---

## LLM-as-judge

Some metrics cannot be computed with exact string matching. Two cases require a judge:

### Semantic accuracy of root-cause description

The Synthesizer's `top_hypothesis.description` should semantically match `ground_truth.true_root_cause_description`. Exact string match is too strict; a hypothesis that says "Version 2.4.1 introduced a null dereference in the payment flow" is correct, even if the wording differs from the ground truth.

```python
async def judge_description_accuracy(
    hypothesis_description: str,
    ground_truth_description: str,
    llm: GenerativeModel,
) -> tuple[bool, str]:
    prompt = f"""
You are evaluating whether a system-generated incident hypothesis correctly identifies
the root cause of a production incident.

Ground truth root cause:
{ground_truth_description}

System hypothesis:
{hypothesis_description}

Does the system hypothesis correctly identify the same root cause as the ground truth?
Answer JSON only: {{"correct": true/false, "reason": "brief explanation"}}
"""
    response = await llm.generate_content_async(prompt)
    return parse_judge_response(response.text)
```

**Judge model:** Use a separate Gemini model instance with `temperature=0` for determinism. Do not use the same model instance or temperature as the agents being evaluated.

### Remediation quality

The system's `recommended_action` should be actionable and consistent with the ground truth remediation. Exact match is not meaningful here.

```python
async def judge_remediation_quality(
    recommended_action: str,
    ground_truth_remediation: str,
    llm: GenerativeModel,
) -> tuple[Literal["correct", "partial", "incorrect"], str]:
    ...
```

LLM-as-judge introduces its own variability. Run the judge at `temperature=0` and on the same prompt multiple times if the result is ambiguous (disagreement on the same input between two judge calls signals a borderline case).

---

## Scoring schema

```python
# evaluations/schemas.py

class IncidentScore(BaseModel):
    incident_id: str
    incident_family: str
    difficulty: str

    # Accuracy
    root_cause_full_match: bool
    root_cause_category_correct: bool
    root_cause_service_correct: bool
    description_accurate: bool          # LLM judge
    remediation_quality: Literal["correct", "partial", "incorrect"]  # LLM judge

    # Timing
    mttfh_seconds: float
    total_latency_seconds: float

    # Efficiency
    tool_calls_total: int
    tool_calls_redundant: int
    tokens_used: int
    estimated_cost_usd: float

    # Quality
    evidence_completeness: bool
    unsupported_claim_rate: float
    top_hypothesis_confidence: float
    safety_guard_triggered: bool
    safety_guard_passed: bool

    # Planning
    required_specialists_called: bool
    specialist_calls_made: list[str]    # ordered list of actual calls

    # Escalation
    escalated: bool
    escalation_appropriate: bool | None  # None if not escalated


class EvalResult(BaseModel):
    run_id: str
    run_timestamp: datetime
    model_id: str                       # which Gemini model was used
    total_incidents: int
    scores: list[IncidentScore]

    # System-level aggregates
    root_cause_accuracy: float          # % full_match
    mean_mttfh_seconds: float
    median_mttfh_seconds: float
    p95_mttfh_seconds: float
    mean_tool_calls: float
    mean_tokens: float
    total_estimated_cost_usd: float
    evidence_completeness_rate: float
    unsupported_claim_rate: float
    safety_guard_trigger_rate: float
    required_specialist_call_rate: float
    escalation_rate: float
    inappropriate_escalations: int

    # Broken down by incident family
    by_family: dict[str, dict]

    # Broken down by difficulty
    by_difficulty: dict[str, dict]
```

---

## Ship thresholds

An agent or the full system is considered ready to ship when it passes all of the following on the 15-incident eval dataset:

| Metric | Threshold | Rationale |
|---|---|---|
| Root-cause accuracy (full match) | ≥ 75% | Primary success metric from Section 1 |
| Evidence completeness | ≥ 80% | Hypothesis must be actionable |
| Unsupported claim rate | < 10% | Safety Guard quality bar |
| L3 actions without approval | 0 | Hard constraint; no threshold - zero tolerance |
| Required specialist call rate | ≥ 90% | Planner must not skip critical investigation steps |
| Confidence calibration | Accuracy ≥ 85% in high-confidence band | Miscalibration is actively dangerous |
| Inappropriate escalations (easy/medium) | 0 | The system must not give up on solvable incidents |

**Soft targets (tracked, no hard gate):**
- MTTFH median < 5 minutes
- Safety Guard trigger rate < 30%
- Mean tool calls < 15 per investigation

The distinction between hard gates and soft targets is intentional. The hard gates protect the on-call engineer from being misled or having actions taken without consent. The soft targets drive prompt optimization and cost reduction but do not block a release.

---

## Regression detection

Every eval run produces an `EvalResult`. Store these as JSON in `evaluations/results/`. When evaluating a PR that touches agent logic or prompts, compare the current run against the stored baseline:

```python
# evaluations/comparison.py

class MetricDelta(BaseModel):
    metric: str
    baseline: float
    current: float
    delta: float
    regressed: bool  # True if current < baseline for accuracy metrics, or > for cost metrics

class ComparisonReport(BaseModel):
    baseline_run_id: str
    current_run_id: str
    regressions: list[MetricDelta]
    improvements: list[MetricDelta]
    unchanged: list[MetricDelta]
    ship_threshold_passed: bool

    def summary(self) -> str:
        ...
```

A PR that improves root-cause accuracy by 5% but increases token cost by 20% is a tradeoff, not a clean improvement - the comparison report makes this visible rather than hiding it in aggregate numbers.

**Regression threshold:** Flag any metric that degrades by more than 5 percentage points relative to baseline. Alert on any hard gate that was previously passing and is now failing.

---

## Phase 2 experiment: with vs. without engineering memory

When Phase 2 is implemented (incident memory write-back to Cloud SQL + pgvector), run the following comparison:

1. **Baseline (no memory):** Run the full 15-incident eval with `similar_incidents = []` forced in all KnowledgeContext outputs, regardless of what pgvector returns. This is the Phase 1 baseline.

2. **Memory-seeded:** Seed the `incident_memory` table with 30+ synthetic historical investigations (covering all three incident families and multiple variants). Re-run the same 15-incident eval with memory enabled.

3. **Measure differential across:**
   - Root-cause accuracy: does memory help?
   - MTTFH: does a similar-incident retrieval let the Planner skip investigation steps?
   - Tool invocations: fewer calls when a past investigation provides a strong prior?
   - Confidence calibration: does memory make the Synthesizer overconfident?

4. **Expected finding:** Improvement should concentrate on incidents that closely match a past investigation (same family, same affected service). Novel incident families or unusual configurations should show no improvement and no degradation.

**What to guard against:** Engineering memory could make the system confidently wrong - if a past investigation had an incorrect root cause and is retrieved as similar, it could bias the Synthesizer. The eval for Phase 2 must include at least 2 "trap" incidents where the most similar past incident had a different root cause, to test that the Synthesizer weights current evidence over historical patterns.

---

## What the harness cannot test

Be explicit about these gaps so they are addressed elsewhere:

| Gap | Why it's a gap | Where it's addressed |
|---|---|---|
| Real API reliability | Fixtures replace real calls; we never test that Cloud Monitoring is actually reachable | Integration tests (separate suite, separate environment) |
| Prompt sensitivity | A small prompt change can shift accuracy significantly; the eval catches the result but not the cause | Prompt versioning and diff tracking in prompts.py |
| Model version changes | Gemini model updates can shift behavior; eval detects the shift but not its cause | Pin model version in GraphConfig; update explicitly |
| Cost at scale | 15 incidents × 1 run is cheap; 100 incidents × daily runs is not | Cost tracking and eval cadence policy (run on merge, not every commit) |
| Concurrent investigation interference | Two investigations running simultaneously should not interfere | Separate concurrency test (not part of accuracy eval) |
