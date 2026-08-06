from datetime import datetime, timezone

from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.graph.llm_tracking import extract_usage, node_usage
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
- After telemetry, call deployment to check for recent changes near the incident onset.
- After deployment, call knowledge to search runbooks and postmortems for matching patterns.
- CRITICAL: Each specialist may only be called ONCE per investigation. The user message shows
  both "Agents called" and a FORBIDDEN ACTIONS list. Obey the FORBIDDEN list exactly - if an
  agent appears there, you MUST NOT invoke it. Choose a different agent or synthesize/escalate.
- If an agent returns no data or empty results, accept that and move to the next uncalled agent.
- Once all three agents have been called, you MUST choose synthesize or escalate - never invoke.
- When invoking, ALWAYS set query.query_type to match the agent field exactly:
    agent="telemetry"   requires  query.query_type="telemetry"
    agent="deployment"  requires  query.query_type="deployment"
    agent="knowledge"   requires  query.query_type="knowledge"
- Set working_confidence based on how well your hypothesis explains all observed symptoms.
- Move to synthesize when confidence >= {threshold} or you have called all available agents.
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
    """
    Build a concise, LLM-readable summary of accumulated evidence.
    De-duplicates by showing only the most recent finding per service per agent type
    so repeated calls don't inflate the prompt with identical blocks.
    """
    parts: list[str] = []

    # Most recent telemetry finding per service
    seen: set[str] = set()
    for f in reversed(state["telemetry_findings"]):
        if f.service in seen:
            continue
        seen.add(f.service)
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

    # Most recent deployment finding per service
    seen = set()
    for f in reversed(state["deployment_findings"]):
        if f.service in seen:
            continue
        seen.add(f.service)
        parts.append(f"[Deployments - {f.service}]")
        parts.append(f.summary)
        parts.append(f"Deployment near onset: {f.deployment_near_onset}")
        if f.nearest_deployment_minutes is not None:
            parts.append(f"Nearest deploy: {f.nearest_deployment_minutes:.0f} min before incident")
        parts.append("")

    # All knowledge queries (each is a distinct search, keep them all)
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

    # Explicit agent call tracking — tells the planner exactly what has and hasn't been invoked
    n_telemetry  = len(state.get("telemetry_findings", []))
    n_deployment = len(state.get("deployment_findings", []))
    n_knowledge  = len(state.get("knowledge_context", []))

    called   = [f"telemetry({n_telemetry}x)"  if n_telemetry  else None,
                f"deployment({n_deployment}x)" if n_deployment else None,
                f"knowledge({n_knowledge}x)"   if n_knowledge  else None]
    uncalled = ["telemetry"  if not n_telemetry  else None,
                "deployment" if not n_deployment else None,
                "knowledge"  if not n_knowledge  else None]

    lines += [
        "INVESTIGATION PROGRESS:",
        f"  Agents called:     {', '.join(c for c in called   if c) or 'none'}",
        f"  Agents not called: {', '.join(u for u in uncalled if u) or 'all called'}",
        "",
    ]

    # Explicit FORBIDDEN list — per-iteration constraints the LLM must not violate
    forbidden = []
    if n_telemetry > 0:
        forbidden.append(f"agent='telemetry' (called {n_telemetry}x — cannot repeat)")
    if n_deployment > 0:
        forbidden.append(f"agent='deployment' (called {n_deployment}x — cannot repeat)")
    if n_knowledge > 0:
        forbidden.append(f"agent='knowledge' (called {n_knowledge}x — cannot repeat)")

    if forbidden:
        lines += [
            "FORBIDDEN ACTIONS (protocol violation to use these):",
        ]
        for f_item in forbidden:
            lines.append(f"  - {f_item}")
        lines.append("")

    # Detect if deployment was called but found nothing near onset (> 60 min gap)
    no_recent_deploy = False
    deploy_gap_str = ""
    if state.get("deployment_findings"):
        dep = state["deployment_findings"][-1]
        no_recent_deploy = (
            dep.nearest_deployment_minutes is not None and dep.nearest_deployment_minutes > 720
        )
        if dep.nearest_deployment_minutes is not None:
            deploy_gap_str = f"{dep.nearest_deployment_minutes:.0f} min before onset"

    if not n_telemetry and not n_deployment and not n_knowledge:
        lines += [
            "REQUIRED NEXT ACTION: invoke telemetry first to establish the symptom baseline.",
            "",
        ]
    elif all([n_telemetry, n_deployment, n_knowledge]):
        lines += [
            "REQUIRED NEXT ACTION: all agents called — you MUST synthesize or escalate now.",
            "",
        ]
    else:
        next_agents = [u for u in uncalled if u]
        if no_recent_deploy and not n_knowledge and n_deployment:
            gap_note = f" (nearest: {deploy_gap_str})" if deploy_gap_str else ""
            lines += [
                f"REQUIRED NEXT ACTION: Deployment agent found no deployment near onset{gap_note} — "
                f"a code change is not the likely cause. For non-deployment-triggered incidents, "
                f"knowledge (runbooks, postmortems) is the primary diagnostic. "
                f"Do NOT synthesize yet — invoke knowledge first.",
                "",
            ]
        else:
            lines += [
                f"REQUIRED NEXT ACTION: invoke one of the uncalled agents: {', '.join(next_agents)}",
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
        temperature=0.1,
    )
    structured_llm = llm.with_structured_output(PlannerDecision, include_raw=True)

    system = _SYSTEM_PROMPT.format(threshold=f"{budget.confidence_threshold:.0%}")
    user_msg = _build_user_message(state, topology)

    raw_result: dict = {}
    try:
        raw_result = await structured_llm.ainvoke([
            ("system", system),
            ("human", user_msg),
        ])
        if raw_result.get("parsing_error"):
            raise ValueError(f"Structured output parse error: {raw_result['parsing_error']}")
        decision: PlannerDecision = raw_result["parsed"]
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

    in_tok, out_tok = extract_usage(raw_result)

    # Increment budget counters
    updated_budget = budget.model_copy(update={
        "iterations_used": budget.iterations_used + 1,
        "tool_calls_used": budget.tool_calls_used + 1,
        "input_tokens_used": budget.input_tokens_used + in_tok,
        "output_tokens_used": budget.output_tokens_used + out_tok,
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
        "token_log": [node_usage("planner", settings.gemini_model, in_tok, out_tok)],
    }

    # Only set topology on state once (first iteration)
    if topology is not None and state.get("service_topology") is None:
        updates["service_topology"] = topology

    if decision.action == "escalate":
        updates["escalation_reason"] = decision.reason

    return updates
