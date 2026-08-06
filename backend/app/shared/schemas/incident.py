from pydantic import BaseModel, ConfigDict, Field


class IncidentTrigger(BaseModel):
    model_config = ConfigDict(extra="ignore")  # raw alert payloads may have unknown fields
    incident_id: str
    alert_name: str
    severity: str  # P1 | P2 | P3
    service_name: str
    onset_timestamp: str  # ISO 8601 string; parsed to datetime in the graph
    description: str
    alert_metadata: dict = Field(default_factory=dict)


class InvestigationBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_iterations: int = 5
    max_tool_calls: int = 20
    max_tokens: int = 50_000
    max_latency_seconds: float = 30.0
    confidence_threshold: float = 0.90

    iterations_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0

    @property
    def iterations_remaining(self) -> int:
        return self.max_iterations - self.iterations_used

    @property
    def tool_calls_remaining(self) -> int:
        return self.max_tool_calls - self.tool_calls_used

    @property
    def is_exhausted(self) -> bool:
        return self.iterations_remaining <= 0 or self.tool_calls_remaining <= 0
