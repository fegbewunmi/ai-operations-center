from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .core import TimelineEvent


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis_id: str
    description: str
    root_cause_category: Literal[
        "deployment", "dependency", "resource",
        "configuration", "infrastructure", "unknown"
    ]
    affected_service: str
    confidence_pct: float  # 0.0-100.0
    supporting_evidence: list[str]
    contradicting_evidence: list[str]
    recommended_action: str
    authority_level: Literal["L1", "L2", "L3"]


class IncidentMemoryRecord(BaseModel):
    """Defined in Phase 1. Written to Cloud SQL + pgvector in Phase 2."""
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    investigation_id: str
    incident_type: Literal[
        "failed_deployment", "latency_regression", "resource_leak", "other"
    ]
    affected_service: str
    onset_timestamp: datetime
    resolution_timestamp: datetime | None = None
    root_cause_category: str
    root_cause_description: str
    confidence_at_resolution: float
    remediation_applied: str | None = None
    investigation_duration_seconds: int
    tool_invocation_count: int
    embedding_text: str
    embedding: list[float] | None = None  # 768 dims; populated in Phase 2


class AnalysisOutput(BaseModel):
    """Produced by the Incident Analysis Agent — ranked hypotheses before formatting."""
    model_config = ConfigDict(extra="forbid")
    hypotheses: list[Hypothesis]  # ranked by confidence_pct desc
    top_hypothesis: Hypothesis
    analysis_complete: bool = True
    requires_escalation: bool = False
    escalation_reason: str | None = None


class SynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    investigation_summary: str
    timeline: list[TimelineEvent] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)  # ranked by confidence_pct desc
    top_hypothesis: Hypothesis
    investigation_incomplete: bool = False
    requires_escalation: bool = False
    escalation_reason: str | None = None
    incident_memory_record: IncidentMemoryRecord | None = None  # None in Phase 1
