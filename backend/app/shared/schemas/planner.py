from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict

from .core import TimeWindow


class TelemetryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str
    time_window: TimeWindow
    investigation_query: str
    focus: list[Literal["metrics", "logs", "traces"]]


class DeploymentQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str
    time_window: TimeWindow
    investigation_query: str


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    service_name: str | None = None
    document_types: list[Literal["runbook", "postmortem", "architecture_doc", "error_pattern"]] | None = None


AgentQuery = Annotated[
    Union[TelemetryQuery, DeploymentQuery, KnowledgeQuery],
    "Query passed from Planner to the target specialist agent"
]


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["invoke", "synthesize", "escalate"]
    agent: Literal["telemetry", "deployment", "knowledge"] | None = None
    query: AgentQuery | None = None
    reason: str  # always populated - primary debugging artifact
    working_hypothesis: str | None = None
    working_confidence: float = 0.0
