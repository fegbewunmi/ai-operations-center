import operator
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.deployment import DeploymentFindings
from app.shared.schemas.incident import IncidentTrigger, InvestigationBudget
from app.shared.schemas.knowledge import KnowledgeContext, ServiceTopology
from app.shared.schemas.planner import PlannerDecision
from app.shared.schemas.response import DispatchedAction, PendingApproval
from app.shared.schemas.synthesis import AnalysisOutput, SynthesisOutput
from app.shared.schemas.telemetry import TelemetryFindings
from app.shared.schemas.validation import ValidationResult


class InvestigationState(TypedDict):
    # Identity
    investigation_id: str
    incident: IncidentTrigger

    # Control flow
    phase: Literal["planning", "investigating", "synthesizing", "responding", "complete", "escalated"]
    planner_decision: PlannerDecision | None
    planner_working_hypothesis: str | None
    planner_working_confidence: float
    budget: InvestigationBudget

    # Accumulated evidence - operator.add means each call appends, not overwrites
    timeline: Annotated[list[TimelineEvent], operator.add]
    telemetry_findings: Annotated[list[TelemetryFindings], operator.add]
    deployment_findings: Annotated[list[DeploymentFindings], operator.add]
    knowledge_context: Annotated[list[KnowledgeContext], operator.add]
    service_topology: ServiceTopology | None

    # Analysis + synthesis outputs
    analysis_output: AnalysisOutput | None   # from Incident Analysis Agent
    synthesis: SynthesisOutput | None        # from Synthesizer Agent
    validation_result: ValidationResult | None

    # Response outputs
    dispatched_actions: Annotated[list[DispatchedAction], operator.add]
    pending_approvals: Annotated[list[PendingApproval], operator.add]

    # Metadata
    started_at: datetime
    completed_at: datetime | None
    escalation_reason: str | None
    error_log: Annotated[list[AgentError], operator.add]
