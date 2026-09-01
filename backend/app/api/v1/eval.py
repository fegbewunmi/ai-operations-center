"""
Evaluation/Observability read endpoints.

Serves exactly what backend/eval/scorer.py already computed and eval/run_eval.py
already wrote to backend/eval_results/*.json — no new metrics are computed here.
Offline-only by design: running the harness is a separate, deliberate step
(`python -m eval.run_eval`, see README) that makes real LLM calls; this router
only reads the committed results.
"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/eval", tags=["evaluation"])

EVAL_RESULTS_DIR = Path(__file__).resolve().parents[3] / "eval_results"


@router.get("/runs")
async def list_eval_runs() -> list[dict]:
    """
    Every committed eval run (backend/eval_results/*.json), most recent first.
    Each run's `results` array is scorer.py's EvalScore fields, one entry per
    fixture, exactly as produced by `python -m eval.run_eval`.
    """
    if not EVAL_RESULTS_DIR.exists():
        return []

    runs: list[dict] = []
    for path in sorted(EVAL_RESULTS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping unreadable eval result file %s: %s", path, exc)
            continue
        data["run_id"] = path.stem
        runs.append(data)

    runs.sort(key=lambda r: r.get("run_at", ""), reverse=True)
    return runs


@router.get("/runs/{run_id}")
async def get_eval_run(run_id: str) -> dict:
    """A single committed eval run by file stem (e.g. 'baseline')."""
    path = EVAL_RESULTS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown eval run '{run_id}'")

    with open(path) as f:
        data = json.load(f)
    data["run_id"] = run_id
    return data
