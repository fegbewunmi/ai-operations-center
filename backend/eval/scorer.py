"""
Evaluation scorer.

Computes the metrics defined in docs/DESIGN-DOC.md Section 6 against the
raw state returned by the runner.

Metrics:
  1  Root-cause accuracy      correct service + category in top hypothesis
  2  MTTFH                    time from start to first synthesis output
  3  Evidence completeness    top hypothesis has >= 2 supporting evidence items
  4  Unsupported claim rate   fraction of evidence citations with no grounding
  5  Required specialist calls all ground-truth calls were made
  6  Confidence calibration   stated confidence vs. accuracy result
  7  Tool call efficiency     redundant tool invocations
  8  Final phase              complete / escalated / other
  9  Investigation incomplete  was the flag set when it should not have been?
  10 Safety Guard trigger rate  did the guard reject synthesis?
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.graph.llm_tracking import llm_cost_usd


@dataclass
class EvalScore:
    incident_id: str
    fixture_path: str
    final_phase: str

    # Accuracy
    root_cause_category_correct: bool
    affected_service_correct: bool
    accuracy: bool  # both correct
    actual_root_cause_category: str | None  # what the model returned
    expected_root_cause_category: str       # ground truth

    # Evidence quality
    evidence_complete: bool  # top hypothesis has >= 2 supporting items
    investigation_incomplete_flag: bool  # was this flagged?

    # Process
    required_specialists_called: bool
    all_specialists: list[str]
    required_specialists: list[str]

    # Timing and cost
    mttfh_seconds: float
    iterations_used: int
    tool_calls_used: int
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float

    # Confidence
    final_confidence: float

    # Safety Guard
    safety_guard_triggered: bool  # validation failed at least once

    # Failures
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.accuracy and self.evidence_complete and self.required_specialists_called


def score(state: dict, fixture_path: Path) -> EvalScore:
    """Compute EvalScore from a final graph state and the fixture used to generate it."""
    with open(fixture_path) as f:
        fixture = json.load(f)

    ground_truth = fixture["ground_truth"]
    incident_id = fixture["incident"]["incident_id"]

    final_phase = state.get("phase", "unknown")
    synthesis = state.get("synthesis")
    budget = state["budget"]
    started_at = state["started_at"]
    completed_at = state.get("completed_at")

    errors: list[str] = []

    # ── Root-cause accuracy ─────────────────────────────────────────────────
    if synthesis is not None and synthesis.top_hypothesis is not None:
        top = synthesis.top_hypothesis
        cat_correct = top.root_cause_category == ground_truth["root_cause_category"]
        svc_correct = top.affected_service == ground_truth["affected_service"]
        final_confidence = top.confidence_pct
        evidence_complete = len(top.supporting_evidence) >= 2
        actual_category = top.root_cause_category
    else:
        cat_correct = False
        svc_correct = False
        final_confidence = 0.0
        evidence_complete = False
        actual_category = None
        errors.append("No synthesis output — investigation did not complete")

    # ── MTTFH ──────────────────────────────────────────────────────────────
    if completed_at and started_at:
        mttfh = (completed_at - started_at).total_seconds()
    else:
        mttfh = float("inf")
        errors.append("No completed_at timestamp — MTTFH cannot be computed")

    # ── Required specialist calls ───────────────────────────────────────────
    required = set(ground_truth.get("required_specialist_calls", []))
    called: set[str] = set()
    for event in state.get("timeline", []):
        src = getattr(event, "source", "")
        if src == "telemetry_agent":
            called.add("telemetry")
        elif src == "deployment_agent":
            called.add("deployment")
        elif src == "knowledge_agent":
            called.add("knowledge")

    specialists_ok = required.issubset(called)

    # ── Escalation reason and error log ────────────────────────────────────
    escalation_reason = state.get("escalation_reason")
    if escalation_reason:
        errors.append(f"Escalation: {escalation_reason}")

    for err in state.get("error_log", []):
        msg = getattr(err, "message", str(err))
        agent = getattr(err, "agent", "unknown")
        errors.append(f"{agent}: {msg}")

    # ── Safety Guard ────────────────────────────────────────────────────────
    validation = state.get("validation_result")
    # Check timeline for any guard failure events
    guard_failed = False
    for event in state.get("timeline", []):
        if getattr(event, "source", "") == "safety_guard":
            desc = getattr(event, "description", "")
            if "FAILED" in desc.upper():
                guard_failed = True
                break

    total_input_tokens = budget.input_tokens_used
    total_output_tokens = budget.output_tokens_used
    estimated_cost_usd = llm_cost_usd(
        settings.gemini_model, total_input_tokens, total_output_tokens
    )

    return EvalScore(
        incident_id=incident_id,
        fixture_path=str(fixture_path),
        final_phase=final_phase,
        root_cause_category_correct=cat_correct,
        affected_service_correct=svc_correct,
        accuracy=cat_correct and svc_correct,
        actual_root_cause_category=actual_category,
        expected_root_cause_category=ground_truth["root_cause_category"],
        evidence_complete=evidence_complete,
        investigation_incomplete_flag=synthesis.investigation_incomplete if synthesis else False,
        required_specialists_called=specialists_ok,
        all_specialists=sorted(called),
        required_specialists=sorted(required),
        mttfh_seconds=mttfh,
        iterations_used=budget.iterations_used,
        tool_calls_used=budget.tool_calls_used,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        final_confidence=final_confidence,
        safety_guard_triggered=guard_failed,
        errors=errors,
    )


def format_score(s: EvalScore) -> str:
    lines = [
        f"  Incident: {s.incident_id}",
        f"  Phase:    {s.final_phase}",
        f"  Accuracy: {'PASS' if s.accuracy else 'FAIL'}"
        f"  (category={s.root_cause_category_correct}, service={s.affected_service_correct})",
        f"  Evidence: {'PASS' if s.evidence_complete else 'FAIL'} (>=2 supporting items)",
        f"  Specialists: {'PASS' if s.required_specialists_called else 'FAIL'}"
        f"  called={s.all_specialists}, required={s.required_specialists}",
        f"  Confidence: {s.final_confidence:.0f}%",
        f"  MTTFH: {s.mttfh_seconds:.0f}s",
        f"  Iterations: {s.iterations_used}  Tool calls: {s.tool_calls_used}",
        f"  Tokens:   {s.total_input_tokens:,} in / {s.total_output_tokens:,} out"
        f"  Cost: ${s.estimated_cost_usd:.4f}",
        f"  Safety Guard triggered: {s.safety_guard_triggered}",
    ]
    if not s.root_cause_category_correct:
        lines.append(
            f"  Category mismatch: expected={s.expected_root_cause_category!r},"
            f" got={s.actual_root_cause_category!r}"
        )
    if s.errors:
        lines.append(f"  ERRORS: {'; '.join(s.errors)}")
    return "\n".join(lines)


def format_summary(scores: list[EvalScore]) -> str:
    n = len(scores)
    if n == 0:
        return "No results."

    accuracy = sum(1 for s in scores if s.accuracy) / n
    evidence = sum(1 for s in scores if s.evidence_complete) / n
    specialists = sum(1 for s in scores if s.required_specialists_called) / n
    complete = sum(1 for s in scores if s.final_phase == "complete") / n
    avg_mttfh = sum(s.mttfh_seconds for s in scores if s.mttfh_seconds != float("inf")) / max(
        1, sum(1 for s in scores if s.mttfh_seconds != float("inf"))
    )
    guard_rate = sum(1 for s in scores if s.safety_guard_triggered) / n
    total_in = sum(s.total_input_tokens for s in scores)
    total_out = sum(s.total_output_tokens for s in scores)
    total_cost = sum(s.estimated_cost_usd for s in scores)

    lines = [
        "",
        "═" * 60,
        f"  EVAL SUMMARY  ({n} incident{'s' if n > 1 else ''})",
        "═" * 60,
        f"  Root-cause accuracy:      {accuracy:.0%}   (target ≥ 75%)",
        f"  Evidence completeness:    {evidence:.0%}   (target ≥ 80%)",
        f"  Required specialists:     {specialists:.0%}  (target ≥ 90%)",
        f"  Completed (not escalated):{complete:.0%}",
        f"  Avg MTTFH:                {avg_mttfh:.0f}s  (target < 300s)",
        f"  Safety Guard trigger rate:{guard_rate:.0%}  (target < 30%)",
        f"  Total tokens:             {total_in:,} in / {total_out:,} out",
        f"  Estimated cost:           ${total_cost:.4f}",
        "─" * 60,
        "  SHIP THRESHOLDS:",
        f"  {'✓' if accuracy >= 0.75  else '✗'} Root-cause accuracy >= 75%: {accuracy:.0%}",
        f"  {'✓' if evidence >= 0.80  else '✗'} Evidence completeness >= 80%: {evidence:.0%}",
        f"  {'✓' if specialists >= 0.90 else '✗'} Required specialists >= 90%: {specialists:.0%}",
        f"  {'✓' if avg_mttfh < 300   else '✗'} Avg MTTFH < 5 min: {avg_mttfh:.0f}s",
        "═" * 60,
    ]
    return "\n".join(lines)
