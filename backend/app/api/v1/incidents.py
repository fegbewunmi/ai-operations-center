"""
Incident Library read endpoints.

`/fixtures` lists the pre-authored, ground-truthed eval scenarios (backend/eval/
fixtures/*.json) that the Incident Library can launch via
POST /v1/investigations/replay/{fixture_id} — see that endpoint for why fixtures,
not live triggers, back the demo scenarios.
"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/incidents", tags=["incidents"])

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "eval" / "fixtures"


@router.get("/fixtures")
async def list_fixtures() -> list[dict]:
    """Launchable fixture scenarios, with their ground truth for reference."""
    if not FIXTURES_DIR.exists():
        return []

    fixtures: list[dict] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping unreadable fixture file %s: %s", path, exc)
            continue

        fixtures.append({
            "fixture_id": path.stem,
            "description": data.get("_comment", ""),
            "incident": data.get("incident", {}),
            "ground_truth": data.get("ground_truth", {}),
        })

    return fixtures
