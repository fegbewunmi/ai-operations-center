from typing import Literal

from pydantic import BaseModel, ConfigDict


class KnowledgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    document_type: Literal["runbook", "postmortem", "architecture_doc", "error_pattern"]
    title: str
    excerpt: str
    relevance_score: float


class SimilarIncident(BaseModel):
    """Phase 2 - populated when incident memory is implemented."""
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    incident_type: str
    affected_service: str
    root_cause_description: str
    remediation_applied: str | None = None
    similarity_score: float
    days_ago: int


class ServiceOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    team: str
    slack_channel: str
    pagerduty_rotation: str | None = None
    runbook_url: str | None = None
    oncall_contact: str | None = None


class ServiceNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    dependencies: list[str] = []
    dependents: list[str] = []
    dependency_types: dict[str, Literal["synchronous", "asynchronous", "optional"]] = {}


class ServiceTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focal_service: str
    nodes: list[ServiceNode] = []
    critical_path: list[str] = []
    blast_radius: list[str] = []


class KnowledgeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    results: list[KnowledgeResult] = []
    ownership: ServiceOwnership | None = None
    similar_incidents: list[SimilarIncident] = []  # empty in Phase 1
    summary: str = ""
    error: str | None = None
