from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .core import TimeWindow


class DeploymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str
    service: str
    version_from: str | None = None
    version_to: str
    deployed_at: datetime
    deployed_by: str
    status: Literal["success", "failed", "rolled_back", "in_progress"]
    config_changes: list[str] = []
    rollback_available: bool = True
    rollback_target_version: str | None = None
    git_commit_sha: str | None = None
    minutes_before_onset: float | None = None  # positive = before incident


class DeploymentFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    time_window: TimeWindow
    query: str
    deployments: list[DeploymentRecord] = []
    deployment_near_onset: bool = False
    nearest_deployment_minutes: float | None = None
    summary: str = ""
    error: str | None = None
