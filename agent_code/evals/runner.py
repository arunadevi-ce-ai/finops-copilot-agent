"""
Runs the real pipeline (detector.py + orchestrator.py, real OpenAI
calls) against each Scenario and scores the outcome. No mocking the LLM
boundary — the point is testing whether the agent's reasoning is
actually correct.
"""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

import detector
import orchestrator
from evals.checks import CheckResult, score_detector_stage, score_orchestrator_stage, score_retrieval_rank
from evals.scenarios import SCENARIOS, Scenario


@dataclass
class ScenarioResult:
    scenario_id: str
    resource_name: str
    detector_category_actual: str | None
    orchestrator_ran: bool
    investigation: dict | None = None
    retrieved_policy_source_files: list[str] | None = None
    recommendation: dict | None = None
    hard_checks: list[CheckResult] = field(default_factory=list)
    soft_checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None

    @property
    def overall_pass(self) -> bool:
        return self.error is None and all(c.passed for c in self.hard_checks)


@dataclass
class ScenarioReliability:
    scenario_id: str
    n_trials: int
    n_passed: int
    pass_rate: float
    trial_results: list[ScenarioResult]


@dataclass
class EvalReport:
    run_timestamp: str
    git_sha: str | None
    results: list[ScenarioResult]
    reliability: dict[str, ScenarioReliability] | None = None  # populated only when repeat > 1

    @property
    def n_hard_pass(self) -> int:
        return sum(1 for r in self.results if r.overall_pass)

    @property
    def n_hard_fail(self) -> int:
        return len(self.results) - self.n_hard_pass

    @property
    def soft_metrics(self) -> dict[str, float]:
        totals: dict[str, int] = {}
        passes: dict[str, int] = {}
        for r in self.results:
            for c in r.soft_checks:
                totals[c.name] = totals.get(c.name, 0) + 1
                passes[c.name] = passes.get(c.name, 0) + (1 if c.passed else 0)
        return {name: passes[name] / totals[name] for name in totals}

    def get_result(self, scenario_id: str) -> ScenarioResult:
        for r in self.results:
            if r.scenario_id == scenario_id:
                return r
        raise KeyError(scenario_id)


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return None


def run_scenario(scenario: Scenario, findings_by_resource: dict, trial_index: int = 0) -> ScenarioResult:
    detector_check = score_detector_stage(scenario, findings_by_resource)
    finding = findings_by_resource.get(scenario.resource_name)

    result = ScenarioResult(
        scenario_id=scenario.id,
        resource_name=scenario.resource_name,
        detector_category_actual=finding.category if finding else None,
        orchestrator_ran=False,
        hard_checks=[detector_check] if detector_check.severity == "hard" else [],
        soft_checks=[detector_check] if detector_check.severity == "soft" else [],
    )

    if not scenario.run_orchestrator:
        return result

    if finding is None:
        result.error = "cannot run orchestrator: resource has no detector Finding"
        return result

    thread_id = f"eval-{scenario.id}-{trial_index}"
    try:
        _, orch_result, _ = orchestrator.run_for_finding(finding, thread_id=thread_id)
    except Exception as e:
        result.error = f"orchestrator.run_for_finding raised: {e!r}"
        return result

    result.orchestrator_ran = True
    result.investigation = orch_result.get("investigation")
    result.retrieved_policy_source_files = [
        p["source_file"] for p in orch_result.get("retrieved_policy", [])
    ]
    result.recommendation = orch_result.get("recommendation")

    orch_checks = score_orchestrator_stage(scenario, orch_result)
    result.hard_checks += [c for c in orch_checks if c.severity == "hard"]
    result.soft_checks += [c for c in orch_checks if c.severity == "soft"]

    query = (orch_result.get("investigation") or {}).get("policy_search_query")
    if query:
        result.soft_checks += score_retrieval_rank(scenario, query)

    return result


def run_all(scenarios: list[Scenario] | None = None, repeat: int = 1) -> EvalReport:
    """repeat only applies to run_orchestrator=True scenarios — detector-only
    scenarios are deterministic, so repeating them is wasted API cost/time
    regardless of the requested N."""
    scenarios = scenarios if scenarios is not None else SCENARIOS
    findings_by_resource = {f.resource_name: f for f in detector.run_detector()}

    results = []
    reliability: dict[str, ScenarioReliability] | None = {} if repeat > 1 else None

    for scenario in scenarios:
        n_trials = repeat if scenario.run_orchestrator else 1
        trial_results = [
            run_scenario(scenario, findings_by_resource, trial_index=i)
            for i in range(n_trials)
        ]
        results.append(trial_results[0])  # canonical result shown in the main report

        if reliability is not None and scenario.run_orchestrator:
            n_passed = sum(1 for r in trial_results if r.overall_pass)
            reliability[scenario.id] = ScenarioReliability(
                scenario_id=scenario.id,
                n_trials=n_trials,
                n_passed=n_passed,
                pass_rate=n_passed / n_trials,
                trial_results=trial_results,
            )

    return EvalReport(
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=_git_sha(),
        results=results,
        reliability=reliability,
    )
