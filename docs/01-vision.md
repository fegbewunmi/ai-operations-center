# Vision — Problem Framing

*Status: Locked. Last updated: 2026-08-06.*

---

## What is this system?

AI Operations Center is an engineering operations platform that reduces Mean Time to Resolution (MTTR) by coordinating production telemetry, deployment intelligence, engineering knowledge, and structured investigation workflows into an automated first-pass root-cause analysis.

The output is not a finished fix. It is a structured, evidence-backed hypothesis delivered fast enough that the on-call engineer's time goes to judgment and action, not data gathering.

---

## Customer environment

**Orion Commerce** — a synthetic mid-size e-commerce platform.

| Service | Role |
|---|---|
| API Gateway | Entry point; routes all external traffic |
| Orders | Core order lifecycle management |
| Payments | Payment processing; calls external payment provider |
| Inventory | Stock tracking; eventually consistent with Orders |
| Notifications | Email/SMS dispatch; downstream, non-critical path |
| User/Auth | Authentication and user profile; cross-cutting |

Service topology: Gateway → Orders → Payments; Orders → Inventory; Orders → Notifications. All runbooks, postmortems, deployment records, ownership data, and incident tickets belong to this environment.

---

## Incident families (Version 1)

Three incident families. Each tests distinct reasoning patterns.

### A. Failed deployment

**Canonical example:** A new Payments release introduces an unhandled exception in the checkout flow. The 5xx error rate increases within minutes of deployment. Logs show a new stack trace absent from prior versions. The previous version was healthy. Rollback restores service.

**What this tests:** Deployment-change correlation, log pattern recognition, change detection, rollback recommendation.

### B. Latency regression

**Canonical example:** A database query change in Orders causes p99 latency to increase. CPU remains normal. Database query duration increases significantly. Downstream services (Payments) begin timing out. No obvious exception exists in application logs.

**What this tests:** Metric reasoning, database-level analysis, dependency tracing, distinguishing symptoms (downstream timeouts) from root cause (slow query).

### C. Resource leak / exhaustion

**Canonical example:** File handles or database connection count in Inventory or Notifications grows gradually. The service appears healthy at first. Failures begin after sustained traffic over hours. A restart temporarily resolves the issue. Logs may misleadingly suggest payload size or timeout errors rather than exhaustion.

**What this tests:** Trend analysis over time, distinguishing misleading surface errors from the underlying cause, gradual degradation patterns.

---

## What a human on-call engineer does today

1. Receives alert (PagerDuty or Slack)
2. Opens metrics dashboard — identifies affected service and when the anomaly started
3. Checks deployment history for changes near the anomaly onset
4. Searches logs for exceptions, volume changes, new error patterns
5. Cross-references known runbooks and past postmortems (rarely, under time pressure)
6. Forms a root-cause hypothesis, typically after 15–45 minutes of manual correlation
7. Recommends or executes remediation; documents findings in the postmortem, hours later

## Where the actual toil is

- Steps 2–5 are mechanical correlation across 3–4 systems with no shared interface
- The same correlation logic repeats on every incident regardless of type
- Institutional knowledge (runbooks, postmortems) is rarely consulted under pressure — it is slow to find and slow to read
- Evidence is not systematically documented; the hypothesis exists only in the engineer's head until the postmortem

---

## Success metrics

| Metric | Target | How measured |
|---|---|---|
| Mean Time to First Hypothesis (MTTFH) | < 5 minutes on synthetic incidents | Eval harness; timestamp of first Synthesizer output |
| Root-cause accuracy | ≥ 75%: correct service + cause category in top-ranked hypothesis | Labeled synthetic incident dataset |
| Evidence completeness | ≥ 80% of hypotheses cite ≥ 2 supporting evidence items | Eval harness; Safety Guard validation output |
| Unsupported claim rate | < 10% of hypothesis claims lack an evidence citation | Safety Guard output |
| Level 3 actions without approval | 0 | Hard constraint enforced by Safety Guard |
| Investigation cost | Tracked per run: token count, tool invocations, wall-clock latency | Eval harness |

---

## What this system is not

- Not a replacement for the on-call engineer — it removes the data-gathering toil, not the judgment
- Not a general-purpose incident responder — Version 1 handles three specific incident families
- Not an autonomous remediation system — Level 3 actions require human approval

---

## Later incident families (planned, not in scope for Version 1)

Configuration drift, dependency outage, queue backlog, authentication/permissions failure, Airflow or background-job failure.
