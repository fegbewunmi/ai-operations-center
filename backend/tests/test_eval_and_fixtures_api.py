"""
Unit tests for the read-only eval-results and fixture-listing endpoints
(app/api/v1/eval.py, app/api/v1/incidents.py). Both routers only glob local JSON
files - no database or LLM calls - so they run as plain unit tests alongside the
node tests, unlike the DB-backed investigation endpoints in test_investigations_api.py.
"""
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.eval import router as eval_router
from app.api.v1.incidents import router as incidents_router
from app.shared.schemas.response import HypothesisFeedback

app = FastAPI()
app.include_router(eval_router)
app.include_router(incidents_router)
client = TestClient(app)


def test_list_eval_runs_returns_committed_baseline():
    resp = client.get("/v1/eval/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert any(r["run_id"] == "baseline" for r in runs)

    baseline = next(r for r in runs if r["run_id"] == "baseline")
    assert "results" in baseline
    incident_ids = {r["incident_id"] for r in baseline["results"]}
    assert incident_ids == {"INC-FD-001", "INC-LR-001", "INC-RL-001"}


def test_get_eval_run_by_id():
    resp = client.get("/v1/eval/runs/baseline")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "baseline"


def test_get_eval_run_unknown_id_404s():
    resp = client.get("/v1/eval/runs/does-not-exist")
    assert resp.status_code == 404


def test_list_fixtures_returns_all_three_scenarios():
    resp = client.get("/v1/incidents/fixtures")
    assert resp.status_code == 200
    fixtures = resp.json()
    fixture_ids = {f["fixture_id"] for f in fixtures}
    assert fixture_ids == {"INC-FD-001", "INC-LR-001", "INC-RL-001"}

    for f in fixtures:
        assert f["incident"]["service_name"]
        assert f["ground_truth"]["root_cause_category"]


def test_hypothesis_feedback_schema_round_trip():
    fb = HypothesisFeedback(
        hypothesis_id="h1",
        verdict="challenged",
        note="disagree with the confidence level",
        submitted_by="ebun",
        submitted_at=datetime.now(timezone.utc),
    )
    dumped = fb.model_dump()
    assert dumped["verdict"] == "challenged"
    assert HypothesisFeedback(**dumped) == fb
