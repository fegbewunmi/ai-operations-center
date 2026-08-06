from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .core import TimeWindow


class TelemetryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_type: Literal["telemetry"] = "telemetry"
    service_name: str
    time_window: TimeWindow
    investigation_query: str
    focus: list[Literal["metrics", "logs", "traces"]]


class DeploymentQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_type: Literal["deployment"] = "deployment"
    service_name: str
    time_window: TimeWindow
    investigation_query: str


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_type: Literal["knowledge"] = "knowledge"
    query: str
    service_name: str | None = None
    document_types: list[Literal["runbook", "postmortem", "architecture_doc", "error_pattern"]] | None = None


AgentQuery = Annotated[
    Union[TelemetryQuery, DeploymentQuery, KnowledgeQuery],
    Field(discriminator="query_type")
]


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["invoke", "synthesize", "escalate"]
    agent: Literal["telemetry", "deployment", "knowledge"] | None = None
    query: AgentQuery | None = None
    reason: str  # always populated - primary debugging artifact
    working_hypothesis: str | None = None
    working_confidence: float = 0.0
    investigation_incomplete: bool = False  # True when synthesizing before confidence threshold
