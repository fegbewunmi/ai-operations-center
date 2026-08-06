from typing import Literal

from pydantic import BaseModel, ConfigDict


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    issues: list[str] = []
    risk_level: Literal["L1", "L2", "L3"]
    unsupported_claims: list[str] = []
    confidence_calibration_ok: bool = True
    action_authority_ok: bool = True
    investigation_incomplete: bool = False
