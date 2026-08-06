from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    end: datetime


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    event_type: Literal[
        "alert",
        "deployment",
        "metric_anomaly",
        "log_event",
        "config_change",
        "investigation_finding",
        "synthesis",
        "approval_requested",
        "approval_granted",
        "action_dispatched",
    ]
    service: str
    description: str
    source: str
    evidence_ref: str | None = None


class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    query_summary: str
    error_type: Literal["tool_failure", "llm_refusal", "validation_error", "timeout"]
    message: str
    timestamp: datetime
    retries_attempted: int
