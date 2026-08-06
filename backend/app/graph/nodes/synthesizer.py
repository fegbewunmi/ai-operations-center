import uuid
from datetime import datetime, timezone

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.graph.state import InvestigationState
from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.synthesis import Hypothesis, SynthesisOutput

_SYSTEM_PROMPT = """
You are the Synthesizer agent in a multi-agent incident investigation system.
Your job is to produce a structured root-cause analysis from the complete evidence set.

You will receive all telemetry, deployment, and knowledge findings gathered during the investigation.
Produce a ranked list of root-cause hypotheses, with the most likely first.

For each hypothesis you must:
- Write a clear, specific description of the failure mode
- Categorise the root cause (deployment, dependency, resource, configuration, infrastructure, unknown)
- Assign a confidence percentage based on how well the evidence supports it
- List the specific pieces of evidence that support it
- List any evidence that contradicts it
- Recommend a concrete action (rollback, restart, page team, etc.)
- Assign authority level: L1 = automated fix safe, L2 = engineer action, L3 = requires approval

Be specific and actionable. Reference exact versions, timestamps, and service names from the evidence.
""".strip()


def _build_evidence_block(state: InvestigationState) -> str:
    incident = state["incident"]
    budget = state["budget"]
    parts = [
        f"INCIDENT: {incident.alert_name}",
        f"  Service: {incident.service_name}",
        f"  Severity: {incident.severity}",
        f"  Onset: {incident.onset_timestamp}",
        f"  Description: {incident.description}",
        f"  Iterations used: {budget.iterations_used}",
        "",
    ]

    for f in state["telemetry_findings"]:
        parts += [f"TELEMETRY [{f.service}]: {f.summary}", ""]

    for f in state["deployment_findings"]:
        parts += [f"DEPLOYMENTS [{f.service}]: {f.summary}"]
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
        parts += [f"KNOWLEDGE [{k.query[:60]}]: {k.summary}"]
        for r in k.results[:3]:
            parts.append(f"  - [{r.document_type}] {r.title} (relevance: {r.relevance_score:.2f})")
        if k.ownership:
            parts.append(f"  Ownership: team={k.ownership.team}, slack={k.ownership.slack_channel}")
        parts.append("")

    if state.get("planner_working_hypothesis"):
        parts += [
            f"PLANNER FINAL HYPOTHESIS: {state['planner_working_hypothesis']}",
            f"PLANNER CONFIDENCE: {state['planner_working_confidence']:.0%}",
            "",
        ]

    return "\n".join(parts)


async def synthesizer_node(state: InvestigationState) -> dict:
    """
    Produces ranked root-cause hypotheses from the complete evidence set.
    Uses structured output to guarantee a valid SynthesisOutput.
    """
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.2,
    )
    structured_llm = llm.with_structured_output(SynthesisOutput)

    evidence = _build_evidence_block(state)
    incident = state["incident"]

    user_msg = (
        f"{evidence}\n\n"
        "Based on all the evidence above, produce a SynthesisOutput with:\n"
        "- 1-3 ranked hypotheses (most likely first)\n"
        "- A concise investigation_summary (3-5 sentences)\n"
        "- top_hypothesis set to the first/most likely hypothesis\n"
        f"- incident_id set to '{incident.incident_id}'\n"
        "- investigation_incomplete=False if confidence is sufficient\n"
        "- requires_escalation=True only if no clear root cause can be determined\n"
    )

    try:
        synthesis: SynthesisOutput = await structured_llm.ainvoke([
            ("system", _SYSTEM_PROMPT),
            ("human", user_msg),
        ])
    except Exception as exc:
        return {
            "error_log": [AgentError(
                agent="synthesizer",
                query_summary="produce synthesis",
                error_type="llm_failure",
                message=str(exc),
                timestamp=datetime.now(timezone.utc),
                retries_attempted=0,
            )],
            "phase": "escalated",
            "escalation_reason": f"Synthesizer failed: {exc}",
        }

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=incident.service_name,
        description=(
            f"Synthesis complete. Top hypothesis: {synthesis.top_hypothesis.description[:120]} "
            f"(confidence: {synthesis.top_hypothesis.confidence_pct:.0f}%)"
        ),
        source="synthesizer",
    )

    return {
        "synthesis": synthesis,
        "phase": "synthesizing",
        "timeline": [event],
        "completed_at": datetime.now(timezone.utc),
    }
