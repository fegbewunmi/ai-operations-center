"""
Synthesizer Agent.

Responsibility: turn the Incident Analysis Agent's validated hypotheses into a
human-readable investigation report. This node does NOT re-derive hypotheses —
it formats what incident_analysis_node already produced.
"""
from datetime import datetime, timezone

from langchain_google_vertexai import ChatVertexAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.graph.state import InvestigationState
from app.graph.tracing import traced_node
from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.synthesis import SynthesisOutput

_SYSTEM_PROMPT = """
You are the Synthesizer in a multi-agent incident investigation system.
The hard reasoning is already done — another agent has produced ranked hypotheses.

Your job: write a concise, human-readable investigation_summary (3-5 sentences) that:
- States the most likely root cause in plain language
- Explains what evidence supports it
- Calls out the recommended action and authority level
- Notes any significant alternative hypotheses

Write for an on-call engineer who has 30 seconds to read it. Be direct. No filler.
""".strip()


def _hypothesis_block(state: InvestigationState) -> str:
    analysis = state["analysis_output"]
    incident = state["incident"]

    parts = [
        f"Incident: {incident.alert_name} on {incident.service_name} ({incident.severity})",
        f"Onset: {incident.onset_timestamp}",
        f"Description: {incident.description}",
        "",
        "Ranked hypotheses from Incident Analysis Agent:",
    ]

    for i, h in enumerate(analysis.hypotheses, 1):  # type: ignore[union-attr]
        parts += [
            f"",
            f"Hypothesis {i}: {h.description}",
            f"  Root cause category: {h.root_cause_category}",
            f"  Confidence: {h.confidence_pct:.0f}%",
            f"  Authority level: {h.authority_level}",
            f"  Recommended action: {h.recommended_action}",
            f"  Supporting evidence: {'; '.join(h.supporting_evidence)}",
        ]
        if h.contradicting_evidence:
            parts.append(f"  Contradicting evidence: {'; '.join(h.contradicting_evidence)}")

    return "\n".join(parts)


@traced_node("synthesizer")
async def synthesizer_node(state: InvestigationState) -> dict:
    """
    Reads analysis_output (hypotheses) from state and writes a human-readable
    investigation summary. Builds the final SynthesisOutput from both.
    """
    analysis = state.get("analysis_output")
    if analysis is None:
        return {
            "error_log": [AgentError(
                agent="synthesizer",
                query_summary="read analysis_output",
                error_type="tool_failure",
                message="analysis_output is None — incident_analysis_node did not produce output",
                timestamp=datetime.now(timezone.utc),
                retries_attempted=0,
            )],
            "phase": "escalated",
            "escalation_reason": "Synthesizer received no analysis output",
        }

    incident = state["incident"]
    llm = ChatVertexAI(
        model_name=settings.gemini_model,
        project=settings.gcp_project_id,
        location=settings.gcp_region,
        temperature=0.3,
    )

    try:
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=_hypothesis_block(state)),
        ])
        summary = response.content.strip()
    except Exception as exc:
        # Non-fatal: fall back to a mechanical summary rather than escalating
        top = analysis.top_hypothesis
        summary = (
            f"Investigation complete. Most likely root cause: {top.description} "
            f"(confidence {top.confidence_pct:.0f}%). "
            f"Recommended action ({top.authority_level}): {top.recommended_action}."
        )

    decision = state.get("planner_decision")
    investigation_incomplete = bool(decision and decision.investigation_incomplete)

    synthesis = SynthesisOutput(
        incident_id=incident.incident_id,
        investigation_summary=summary,
        hypotheses=analysis.hypotheses,
        top_hypothesis=analysis.top_hypothesis,
        requires_escalation=analysis.requires_escalation,
        escalation_reason=analysis.escalation_reason,
        investigation_incomplete=investigation_incomplete,
    )

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=incident.service_name,
        description=(
            f"Synthesis complete. "
            f"Top: {analysis.top_hypothesis.description[:100]} "
            f"({analysis.top_hypothesis.confidence_pct:.0f}% confidence, "
            f"{analysis.top_hypothesis.authority_level})"
        ),
        source="synthesizer",
    )

    return {
        "synthesis": synthesis,
        "phase": "synthesizing",
        "timeline": [event],
        "completed_at": datetime.now(timezone.utc),
    }
