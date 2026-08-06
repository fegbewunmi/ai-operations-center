from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .core import TimeWindow


class MetricPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: float
    unit: str
    timestamp: datetime
    is_anomalous: bool
    baseline_value: float | None = None


class LogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    message: str
    service: str
    trace_id: str | None = None
    count: int = 1


class ResourceUtilization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpu_pct: float | None = None
    memory_pct: float | None = None
    connection_count: int | None = None
    fd_count: int | None = None
    thread_count: int | None = None


class TelemetryFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    time_window: TimeWindow
    query: str
    key_metrics: list[MetricPoint] = []
    anomalous_metrics: list[MetricPoint] = []
    log_events: list[LogEvent] = []
    resource_utilization: ResourceUtilization = ResourceUtilization()
    error_rate_change_pct: float | None = None
    latency_p99_change_pct: float | None = None
    summary: str = ""
    error: str | None = None
