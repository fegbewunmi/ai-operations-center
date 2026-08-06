from typing import Literal

from pydantic import BaseModel, ConfigDict


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    issues: list[str] = []

    # Per-check breakdown — lets the planner know exactly which dimension failed
    confidence_ok: bool = True
    evidence_grounded: bool = True  # sufficient supporting evidence items
    sources_aligned: bool = True    # hypothesis category matches gathered evidence
    authority_ok: bool = True       # L3 actions have a clear directive

    risk_level: Literal["L1", "L2", "L3"]
    investigation_incomplete: bool = False
