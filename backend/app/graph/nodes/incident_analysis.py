"""
Incident Analysis Agent.

Responsibility: correlate all gathered evidence into a ranked set of root-cause
hypotheses. This is the "what went wrong?" reasoning step — it does NOT produce
the human-readable investigation report (that's the Synthesizer's job).
"""
import uuid
from datetime import datetime, timezone

from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import settings
from app.graph.state import InvestigationState
from app.graph.tracing import traced_node
from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.synthesis import AnalysisOutput, Hypothesis

_SYSTEM_PROMPT = """
You are the Incident Analysis Agent in a multi-agent incident investigation system.

Your single responsibility: correlate all gathered evidence and produce a ranked list
of root-cause hypotheses. You are NOT writing a human-readable report — that comes next.
You ARE doing the hard reasoning work of connecting symptoms to causes.

Root cause category definitions — classify by failure mechanism, not just trigger:
  deployment:      A code release caused IMMEDIATE regression at or near deployment time.
                   Symptoms started within 30 minutes of deployment. Rollback is the
                   primary immediate remediation.
  resource:        The system exhausted a finite pool (connections, memory, CPU, file handles).
                   The exhaustion may have been triggered by a deployment bug, but the failure
                   mechanism is depletion building over time toward a hard limit — symptoms
                   appear hours after the triggering event, not immediately.
  dependency:      An upstream or downstream service degraded and propagated failures here.
  configuration:   A schema object, setting, or parameter change caused the regression —
                   e.g. dropped index, altered constraint, changed config flag. The system
                   is correctly resourced but mis-configured or mis-schemaed.
  infrastructure:  Underlying infrastructure (network, load balancer, availability zone) failed.
  unknown:         Evidence is insufficient to categorize.

For each hypothesis you must:
- Write a clear, specific description of the failure mode
- Categorise the root cause using ONLY one of the six categories defined above
- Assign a confidence percentage (0-100) based strictly on evidence strength
- List the exact evidence items that support it (quote specific values, timestamps, versions)
- List any evidence that contradicts it
- Recommend a concrete, specific action (e.g. "Roll back payments service to v2.3.1")
- Assign authority level: L1 = automated fix safe, L2 = engineer action, L3 = requires approval

Ranking rules:
- Rank by confidence descending; highest-confidence hypothesis is first
- If a deployment occurred within 30 minutes of onset, it should appear as a hypothesis
  unless telemetry clearly contradicts it
- Do not hallucinate evidence; only cite what appears in the input
- If confidence in all hypotheses is < 40%, set requires_escalation=True

Be specific. Reference exact versions, timestamps, service names, and metric values.
""".strip()


def _build_evidence_block(state: InvestigationState) -> str:
    incident = state["incident"]
    budget = state["budget"]

    parts = [
        f"INCIDENT: {incident.alert_name}",
        f"  Service:     {incident.service_name}",
        f"  Severity:    {incident.severity}",
        f"  Onset:       {incident.onset_timestamp}",
        f"  Description: {incident.description}",
        f"  Budget used: {budget.iterations_used} iterations, {budget.tool_calls_used} tool calls",
        "",
    ]

    topology = state.get("service_topology")
    if topology and topology.nodes:
        node = topology.nodes[0]
        parts += [
            f"SERVICE TOPOLOGY",
            f"  Calls:       {', '.join(node.dependencies) or 'none'}",
            f"  Called by:   {', '.join(node.dependents) or 'none'}",
            f"  Blast radius: {', '.join(topology.blast_radius) or 'none'}",
            "",
        ]

    for f in state["telemetry_findings"]:
        parts.append(f"TELEMETRY [{f.service}]:")
        parts.append(f"  {f.summary}")
        if f.anomalous_metrics:
            for m in f.anomalous_metrics:
                parts.append(f"  Anomaly: {m.name} = {m.value} (baseline {m.baseline_value})")
        if f.error_rate_change_pct is not None:
            parts.append(f"  Error rate change: {f.error_rate_change_pct:+.1f}%")
        if f.latency_p99_change_pct is not None:
            parts.append(f"  p99 latency change: {f.latency_p99_change_pct:+.1f}%")
        parts.append("")

    for f in state["deployment_findings"]:
        parts.append(f"DEPLOYMENTS [{f.service}]:")
        parts.append(f"  {f.summary}")
        if f.deployment_near_onset and f.nearest_deployment_minutes is not None:
            parts.append(f"  *** Deployment {f.nearest_deployment_minutes:.0f} min before onset ***")
        for d in f.deployments:
            changes = ", ".join(d.config_changes) if d.config_changes else "none"
            parts.append(
                f"  - {d.version_to} by {d.deployed_by} "
                f"({d.minutes_before_onset:.0f} min before onset, changes: {changes})"
            )
        parts.append("")

    for k in state["knowledge_context"]:
        parts.append(f"KNOWLEDGE [{k.query[:60]}]:")
        parts.append(f"  {k.summary}")
        for r in k.results[:3]:
            parts.append(f"  - [{r.document_type}] {r.title} (relevance: {r.relevance_score:.2f})")
            if r.excerpt:
                parts.append(f"    Excerpt: {r.excerpt[:300]}")
        if k.ownership:
            parts.append(
                f"  Ownership: team={k.ownership.team}, slack={k.ownership.slack_channel}"
            )
        parts.append("")

    if state.get("planner_working_hypothesis"):
        parts += [
            f"PLANNER WORKING HYPOTHESIS: {state['planner_working_hypothesis']}",
            f"PLANNER CONFIDENCE: {state['planner_working_confidence']:.0%}",
            "",
        ]

    return "\n".join(parts)


@traced_node("incident_analysis")
async def incident_analysis_node(state: InvestigationState) -> dict:
    """
    Correlates all evidence into ranked root-cause hypotheses.
    Always flows to synthesizer_node next.
    """
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.15,
    )
    structured_llm = llm.with_structured_output(AnalysisOutput)

    evidence = _build_evidence_block(state)
    incident = state["incident"]

    user_msg = (
        f"{evidence}\n\n"
        "Produce an AnalysisOutput with:\n"
        "- 1-3 hypotheses ranked by confidence (highest first)\n"
        "- top_hypothesis set to the most likely one\n"
        "- requires_escalation=True only if max confidence < 40%\n"
        f"- Each hypothesis.hypothesis_id should be a short unique string like 'h1', 'h2'\n"
        f"- Each hypothesis.affected_service should be '{incident.service_name}' unless evidence clearly points elsewhere\n"
        "- Cite specific evidence: timestamps, versions, metric values"
    )

    try:
        analysis: AnalysisOutput = await structured_llm.ainvoke([
            ("system", _SYSTEM_PROMPT),
            ("human", user_msg),
        ])
    except Exception as exc:
        return {
            "error_log": [AgentError(
                agent="incident_analysis",
                query_summary="correlate evidence into hypotheses",
                error_type="tool_failure",
                message=str(exc),
                timestamp=datetime.now(timezone.utc),
                retries_attempted=0,
            )],
            "phase": "escalated",
            "escalation_reason": f"Incident Analysis Agent failed: {exc}",
        }

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=incident.service_name,
        description=(
            f"Incident analysis: {len(analysis.hypotheses)} hypothesis(es). "
            f"Top: {analysis.top_hypothesis.description[:100]} "
            f"(confidence: {analysis.top_hypothesis.confidence_pct:.0f}%)"
        ),
        source="incident_analysis",
    )

    phase = "escalated" if analysis.requires_escalation else "synthesizing"

    result: dict = {
        "analysis_output": analysis,
        "phase": phase,
        "timeline": [event],
    }

    if analysis.requires_escalation:
        result["escalation_reason"] = analysis.escalation_reason or "Analysis confidence too low"

    return result
