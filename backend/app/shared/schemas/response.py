from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DispatchedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: Literal["jira_ticket", "slack_message", "pagerduty_incident", "pagerduty_update"]
    external_id: str
    url: str | None = None
    dispatched_at: datetime
    authority_level: Literal["L1", "L2"]


class PendingApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str
    investigation_id: str
    action_description: str
    authority_level: Literal["L3"]
    checkpoint_id: str
    requested_at: datetime
    approved: bool | None = None  # None = pending
    approved_by: str | None = None
    approved_at: datetime | None = None
    notes: str | None = None
