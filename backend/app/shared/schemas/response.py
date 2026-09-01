from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DispatchedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: Literal["jira_ticket", "slack_message", "pagerduty_incident", "pagerduty_update"]
    external_id: str
    url: str | None = None
    dispatched_at: datetime
    authority_level: Literal["L1", "L2", "L3"]


class HypothesisFeedback(BaseModel):
    """
    Human judgment on a specific hypothesis, captured for usability-test analysis.
    'challenged' additionally triggers a real planner resume (see
    submit_hypothesis_feedback in app/api/v1/investigations.py) — accepted/rejected
    are pure signal capture and do not change investigation state.
    """
    model_config = ConfigDict(extra="forbid")
    hypothesis_id: str
    verdict: Literal["accepted", "rejected", "challenged"]
    note: str | None = None
    submitted_by: str
    submitted_at: datetime


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
