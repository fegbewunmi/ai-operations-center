#!/usr/bin/env python3
"""
AI Operations Center — Evaluation harness CLI.

Runs the investigation graph against labeled synthetic incident fixtures and
scores accuracy against ground truth. All LLM calls are real; external APIs
(Cloud Monitoring, deployment DB, knowledge DB) are replaced by fixture data.

Requirements:
    - GEMINI_API_KEY env var must be set
    - DATABASE_URL is NOT required (all external calls are mocked)

Usage:
    # Run all fixtures
    python -m eval.run_eval

    # Run a specific fixture by incident ID
    python -m eval.run_eval --fixture INC-FD-001

    # Run all fixtures and write JSON results
    python -m eval.run_eval --output eval_results.json
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the backend package is on the path when run from the backend/ dir
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.runner import run_fixture
from eval.scorer import EvalScore, format_score, format_summary, score


FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def run_one(fixture_path: Path) -> tuple[dict, EvalScore]:
    print(f"\n{'─' * 60}")
    print(f"  Running: {fixture_path.name}")
    print(f"{'─' * 60}")

    state = await run_fixture(fixture_path)
    s = score(state, fixture_path)
    print(format_score(s))
    return state, s


async def main(fixture_id: str | None = None, output_path: str | None = None) -> None:
    if fixture_id:
        paths = [FIXTURES_DIR / f"{fixture_id}.json"]
        missing = [p for p in paths if not p.exists()]
        if missing:
            print(f"ERROR: fixture not found: {missing[0]}")
            sys.exit(1)
    else:
        paths = sorted(FIXTURES_DIR.glob("*.json"))
        if not paths:
            print(f"ERROR: no fixture files found in {FIXTURES_DIR}")
            sys.exit(1)

    print(f"\nAI Operations Center — Evaluation Harness")
    print(f"Running {len(paths)} fixture(s)  ·  {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")

    results: list[tuple[dict, EvalScore]] = []
    for path in paths:
        state, s = await run_one(path)
        results.append((state, s))

    scores = [s for _, s in results]
    print(format_summary(scores))

    if output_path:
        out = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "results": [
                {
                    "incident_id": s.incident_id,
                    "fixture_path": s.fixture_path,
                    "final_phase": s.final_phase,
                    "accuracy": s.accuracy,
                    "root_cause_category_correct": s.root_cause_category_correct,
                    "affected_service_correct": s.affected_service_correct,
                    "evidence_complete": s.evidence_complete,
                    "required_specialists_called": s.required_specialists_called,
                    "final_confidence": s.final_confidence,
                    "mttfh_seconds": s.mttfh_seconds if s.mttfh_seconds != float("inf") else None,
                    "iterations_used": s.iterations_used,
                    "tool_calls_used": s.tool_calls_used,
                    "safety_guard_triggered": s.safety_guard_triggered,
                    "errors": s.errors,
                }
                for s in scores
            ],
        }
        with open(output_path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nResults written to: {output_path}")

    failed_thresholds = [s for s in scores if not s.passed]
    sys.exit(1 if failed_thresholds else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Operations Center evaluation harness")
    parser.add_argument(
        "--fixture",
        type=str,
        default=None,
        help="Run a specific fixture by incident ID (e.g. INC-FD-001). Runs all if omitted.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write JSON results to this file path.",
    )
    args = parser.parse_args()
    asyncio.run(main(fixture_id=args.fixture, output_path=args.output))
