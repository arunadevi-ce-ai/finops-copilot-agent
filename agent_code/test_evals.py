"""
pytest wiring for the eval harness (see evals/ package). Runs the real
pipeline once per session, including the 2 LLM-backed scenarios,
independent of test_orchestrator.py's own fixture — a full `pytest -q`
run makes those 2 gpt-4o-mini calls twice, once per file.

History (evals/results/history.csv) is only appended when
EVAL_TRACK_HISTORY=1 is set, so routine pytest runs don't spam the
trend file. `python -m evals.run` always writes by default.
"""

import os

import pytest

from evals.report import append_csv_history
from evals.runner import run_all
from evals.scenarios import SCENARIOS


@pytest.fixture(scope="module")
def eval_report():
    report = run_all()
    if os.environ.get("EVAL_TRACK_HISTORY") == "1":
        append_csv_history(report)
    return report


@pytest.mark.parametrize("scenario_id", [s.id for s in SCENARIOS])
def test_scenario_hard_checks_pass(eval_report, scenario_id):
    result = eval_report.get_result(scenario_id)
    failed = [c for c in result.hard_checks if not c.passed]

    assert result.error is None, f"{scenario_id} errored: {result.error}"
    assert not failed, f"{scenario_id} failed hard checks: {failed}"


def test_soft_metrics_have_not_collapsed(eval_report):
    """Generous floors — only fires on a real collapse (e.g. retrieval
    breaking entirely), not ordinary LLM-to-LLM citation variance."""
    metrics = eval_report.soft_metrics

    if "retrieval_top3" in metrics:
        assert metrics["retrieval_top3"] >= 0.5
    if "citation_in_acceptable_set" in metrics:
        assert metrics["citation_in_acceptable_set"] >= 0.3
