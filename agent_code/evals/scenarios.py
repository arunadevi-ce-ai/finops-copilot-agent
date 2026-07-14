"""
Golden scenarios for the eval harness — formalizes ground truth that
already exists in test_detector.py and test_orchestrator.py into a
single, importable list. Doesn't invent new expectations; doesn't
duplicate the ground truth a third time (EXPECTED_ANOMALIES and
CLEAN_RESOURCES are imported directly from test_detector.py, not
hand-copied).
"""

from dataclasses import dataclass

from test_detector import CLEAN_RESOURCES, EXPECTED_ANOMALIES

# The DR-volume trap has its own orchestrator-level ground truth (it's
# the point of the whole project) and isn't a plain detector-only case.
_TRAP_RESOURCE = "pd-payments-dr-volume-01"
_IDLE_GPU_RESOURCE = "vm-ml-training-gpu-01"


@dataclass(frozen=True)
class Scenario:
    id: str
    resource_name: str
    description: str

    expected_detector_category: str | None = None  # None = must NOT appear in findings

    run_orchestrator: bool = False  # gates the costlier, real-LLM path
    expect_manual_review: bool | None = None
    expect_no_deletion: bool = False
    require_citation_mentions_precedent: bool = False
    expected_citation_files_hard: tuple[str, ...] | None = None
    acceptable_citation_files_soft: tuple[str, ...] | None = None


def _detector_only_scenarios() -> list[Scenario]:
    scenarios = []
    for resource_name, category in EXPECTED_ANOMALIES.items():
        if resource_name in (_TRAP_RESOURCE, _IDLE_GPU_RESOURCE):
            continue  # both handled separately below, with orchestrator-level ground truth
        scenarios.append(Scenario(
            id=resource_name.replace("-", "_"),
            resource_name=resource_name,
            description=f"detector.py must categorize this as {category}",
            expected_detector_category=category,
        ))

    for resource_name in CLEAN_RESOURCES:
        scenarios.append(Scenario(
            id=resource_name.replace("-", "_"),
            resource_name=resource_name,
            description="must never be flagged by detector.py",
            expected_detector_category=None,
        ))
    return scenarios


SCENARIOS: list[Scenario] = [
    *_detector_only_scenarios(),
    Scenario(
        id="dr_volume_trap",
        resource_name=_TRAP_RESOURCE,
        description=(
            "The naive detector flags this as orphaned_disk (correct for that "
            "layer). The orchestrator must catch the purpose=disaster-recovery "
            "tag via the retrieved Precedent section and downgrade to manual "
            "review instead of proposing deletion."
        ),
        expected_detector_category=EXPECTED_ANOMALIES[_TRAP_RESOURCE],
        run_orchestrator=True,
        expect_manual_review=True,
        expect_no_deletion=True,
        require_citation_mentions_precedent=True,
        expected_citation_files_hard=("policy-fin-07-orphaned-resources.md",),
    ),
    Scenario(
        id="idle_gpu_cost_spike",
        resource_name=_IDLE_GPU_RESOURCE,
        description=(
            "A real cost-spike case (GPU left running 24/7). Only structural "
            "checks are hard here — which policy doc gets cited legitimately "
            "varies run to run (policy-fin-04 vs. policy-fin-11 are both "
            "defensible), so citation is tracked as a soft metric, not pinned."
        ),
        expected_detector_category=EXPECTED_ANOMALIES[_IDLE_GPU_RESOURCE],
        run_orchestrator=True,
        acceptable_citation_files_soft=(
            "policy-fin-04-rightsizing.md",
            "policy-fin-11-idle-gpu.md",
        ),
    ),
]
