from datetime import datetime, timezone

from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.graph.state import InvestigationState
from app.graph.tracing import traced_node
from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.knowledge import ServiceNode, ServiceTopology
from app.shared.schemas.planner import PlannerDecision

_SYSTEM_PROMPT = """
You are the Planner agent in a multi-agent incident investigation system for Orion Commerce,
a synthetic e-commerce platform. Your job is to direct a systematic root cause investigation.

You control three specialist agents:
- telemetry: queries Cloud Monitoring metrics, Cloud Logging, and traces for a service
- deployment: checks deployment history and config changes near the incident onset
- knowledge: retrieves runbooks, postmortems, and architecture docs via semantic search

Each iteration you receive the full investigation state and must output one decision:
  action=invoke   -> call a specialist (set agent and query fields)
  action=synthesize -> you have enough evidence; call the Synthesizer
  action=escalate -> investigation is stuck or budget is nearly exhausted

Rules:
- Start with telemetry on the affected service to establish the symptom baseline.
- Check deployment history if you see sudden changes or if timing is suspicious.
- Use knowledge search to find runbooks or similar past incidents.
- Never repeat a query you have already made - each call must explore new evidence.
- If telemetry returns no data or says "not instrumented", do NOT retry telemetry - move to deployment or knowledge instead.
- If an agent returns empty results, accept that and move on to the next evidence source.
- Set working_confidence based on how well your hypothesis explains all symptoms observed.
- Move to synthesize when confidence >= {threshold} or you have covered all evidence angles.
- You MUST synthesize if you have checked telemetry, deployment, AND knowledge - do not keep invoking agents after all three have been queried.
- Always populate the reason field - it is the primary artifact for debugging and evaluation.
""".strip()


async def _fetch_topology(service_name: str) -> ServiceTopology | None:
    """Query Cloud SQL for the dependency graph around the affected service."""
    async with AsyncSessionLocal() as db:
        svc_row = await db.execute(
            text("SELECT service_id FROM services WHERE name = :name"),
            {"name": service_name},
        )
        svc = svc_row.fetchone()
        if svc is None:
            return None
        service_id = str(svc[0])

        # Services the focal service calls (downstream)
        dep_rows = await db.execute(
            text("""
                SELECT s.name, sd.dependency_type
                FROM service_dependencies sd
                JOIN services s ON s.service_id = sd.downstream_service_id
                WHERE sd.upstream_service_id = :sid
            """),
            {"sid": service_id},
        )
        dependencies = [(r.name, r.dependency_type) for r in dep_rows]

        # Services that call the focal service (upstream)
        dep_on_rows = await db.execute(
            text("""
                SELECT s.name, sd.dependency_type
                FROM service_dependencies sd
                JOIN services s ON s.service_id = sd.upstream_service_id
                WHERE sd.downstream_service_id = :sid
            """),
            {"sid": service_id},
        )
        dependents = [(r.name, r.dependency_type) for r in dep_on_rows]

    focal_node = ServiceNode(
        service=service_name,
        dependencies=[d[0] for d in dependencies],
        dependents=[u[0] for u in dependents],
        dependency_types={n: t for n, t in dependencies + dependents},
    )

    # Blast radius: callers that will degrade if this service fails
    blast_radius = [u[0] for u in dependents if u[1] == "synchronous"]

    # Critical path: synchronous callers -> focal service
    critical_path = [u[0] for u in dependents if u[1] == "synchronous"] + [service_name]

    return ServiceTopology(
        focal_service=service_name,
        nodes=[focal_node],
        critical_path=critical_path,
        blast_radius=blast_radius,
    )


def _evidence_summary(state: InvestigationState) -> str:
    """Build a concise, LLM-readable summary of all evidence gathered so far."""
    parts: list[str] = []

    for f in state["telemetry_findings"]:
        parts.append(f"[Telemetry - {f.service}]")
        parts.append(f.summary)
        if f.anomalous_metrics:
            items = [f"{m.name}: {m.value} (baseline {m.baseline_value})" for m in f.anomalous_metrics]
            parts.append(f"Anomalies: {', '.join(items)}")
        if f.error_rate_change_pct is not None:
            parts.append(f"Error rate change: {f.error_rate_change_pct:+.1f}%")
        if f.latency_p99_change_pct is not None:
            parts.append(f"p99 latency change: {f.latency_p99_change_pct:+.1f}%")
        parts.append("")

    for f in state["deployment_findings"]:
        parts.append(f"[Deployments - {f.service}]")
        parts.append(f.summary)
        parts.append(f"Deployment near onset: {f.deployment_near_onset}")
        if f.nearest_deployment_minutes is not None:
            parts.append(f"Nearest deploy: {f.nearest_deployment_minutes:.0f} min before incident")
        parts.append("")

    for k in state["knowledge_context"]:
        parts.append(f"[Knowledge - query: {k.query}]")
        parts.append(k.summary)
        if k.results:
            top = [f"{r.document_type}: {r.title}" for r in k.results[:3]]
            parts.append(f"Docs retrieved: {', '.join(top)}")
        parts.append("")

    return "\n".join(parts) if parts else "No evidence gathered yet."


def _build_user_message(state: InvestigationState, topology: ServiceTopology | None) -> str:
    incident = state["incident"]
    budget = state["budget"]

    lines = [
        f"INCIDENT: {incident.alert_name}",
        f"  Service:     {incident.service_name}",
        f"  Severity:    {incident.severity}",
        f"  Onset:       {incident.onset_timestamp}",
        f"  Description: {incident.description}",
        "",
        f"BUDGET REMAINING: {budget.iterations_remaining} iterations, "
        f"{budget.tool_calls_remaining} tool calls",
        "",
    ]

    if topology and topology.nodes:
        node = topology.nodes[0]
        lines += [
            f"SERVICE TOPOLOGY (focal: {topology.focal_service})",
            f"  Calls:       {', '.join(node.dependencies) or 'none'}",
            f"  Called by:   {', '.join(node.dependents) or 'none'}",
            f"  Blast radius (services degraded if this one fails): "
            f"{', '.join(topology.blast_radius) or 'none'}",
            "",
        ]

    if state["planner_working_hypothesis"]:
        lines += [
            f"CURRENT HYPOTHESIS: {state['planner_working_hypothesis']}",
            f"CURRENT CONFIDENCE: {state['planner_working_confidence']:.0%}",
            "",
        ]

    validation = state.get("validation_result")
    if validation is not None and not validation.passed:
        lines += [
            "PREVIOUS SYNTHESIS FAILED SAFETY GUARD:",
            f"  Issues: {'; '.join(validation.issues)}",
            "  You must gather additional evidence to resolve these issues before synthesizing again.",
            "  Do NOT synthesize again with the same confidence level.",
            "",
        ]

    lines += [
        "ACCUMULATED EVIDENCE:",
        _evidence_summary(state),
    ]

    return "\n".join(lines)


@traced_node("planner")
async def planner_node(state: InvestigationState) -> dict:
    """
    Controls the investigation loop.
    Decides which specialist to invoke next, or when evidence is sufficient to synthesize.
    """
    budget = state["budget"]

    # Hard stop - never call the LLM if budget is already exhausted
    if budget.is_exhausted:
        decision = PlannerDecision(
            action="escalate",
            reason="Investigation budget exhausted before reaching confidence threshold.",
            working_hypothesis=state.get("planner_working_hypothesis"),
            working_confidence=state.get("planner_working_confidence", 0.0),
        )
        return {
            "planner_decision": decision,
            "phase": "escalated",
            "escalation_reason": decision.reason,
        }

    # Fetch topology on first iteration only
    topology: ServiceTopology | None = state.get("service_topology")
    if topology is None:
        try:
            topology = await _fetch_topology(state["incident"].service_name)
        except Exception as exc:
            return {
                "error_log": [AgentError(
                    agent="planner",
                    query_summary="fetch service topology",
                    error_type="tool_failure",
                    message=str(exc),
                    timestamp=datetime.now(timezone.utc),
                    retries_attempted=0,
                )]
            }

    # Call Gemini with structured output
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.1,
    )
    structured_llm = llm.with_structured_output(PlannerDecision)

    system = _SYSTEM_PROMPT.format(threshold=f"{budget.confidence_threshold:.0%}")
    user_msg = _build_user_message(state, topology)

    try:
        decision: PlannerDecision = await structured_llm.ainvoke([
            ("system", system),
            ("human", user_msg),
        ])
    except Exception as exc:
        error_type = "llm_refusal" if "refused" in str(exc).lower() else "tool_failure"
        # Escalate rather than loop - returning without phase change would re-enter planner forever
        return {
            "error_log": [AgentError(
                agent="planner",
                query_summary="planner LLM decision",
                error_type=error_type,
                message=str(exc),
                timestamp=datetime.now(timezone.utc),
                retries_attempted=0,
            )],
            "phase": "escalated",
            "escalation_reason": f"Planner LLM call failed: {exc}",
            "planner_decision": PlannerDecision(
                action="escalate",
                reason=f"LLM unavailable: {exc}",
                working_hypothesis=state.get("planner_working_hypothesis"),
                working_confidence=state.get("planner_working_confidence", 0.0),
            ),
        }

    # Increment budget counters
    updated_budget = budget.model_copy(update={
        "iterations_used": budget.iterations_used + 1,
        "tool_calls_used": budget.tool_calls_used + 1,
    })

    phase_map = {"invoke": "investigating", "synthesize": "synthesizing", "escalate": "escalated"}

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=state["incident"].service_name,
        description=f"Planner: {decision.action} - {decision.reason}",
        source="planner",
    )

    updates: dict = {
        "planner_decision": decision,
        "planner_working_hypothesis": decision.working_hypothesis,
        "planner_working_confidence": decision.working_confidence,
        "budget": updated_budget,
        "phase": phase_map[decision.action],
        "timeline": [event],
    }

    # Only set topology on state once (first iteration)
    if topology is not None and state.get("service_topology") is None:
        updates["service_topology"] = topology

    if decision.action == "escalate":
        updates["escalation_reason"] = decision.reason

    return updates
