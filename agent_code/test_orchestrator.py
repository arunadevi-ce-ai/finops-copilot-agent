"""
Runs orchestrator.py's real LangGraph graph against the real OpenAI
API — not mocked, since the whole point of this graph is the LLM
reasoning itself. Prints full agent output for every case so results
are inspectable, not just pass/fail.
"""

import pytest
from langgraph.types import Command

import detector
from orchestrator import run_for_finding


@pytest.fixture(scope="module")
def findings_by_resource():
    return {f.resource_name: f for f in detector.run_detector()}


def _print_case(label: str, result: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"Investigation: {result['investigation']}")
    print("Retrieved policy sections:")
    for r in result["retrieved_policy"]:
        print(f"  {r['source_file']} :: {r['section_title']}")
    print(f"Recommendation: {result['recommendation']}")


def test_dr_volume_trap_flags_manual_review_not_deletion(findings_by_resource):
    """detector.py alone flags this as a plain orphaned_disk (see
    test_detector.py). The full graph, grounded in policy-fin-07's
    Precedent section, must catch the disaster-recovery tag instead
    of proposing deletion."""
    finding = findings_by_resource["pd-payments-dr-volume-01"]
    _, result, _ = run_for_finding(finding, thread_id="test-dr-volume-trap")
    _print_case("DR-volume trap case (pd-payments-dr-volume-01)", result)

    assert "__interrupt__" in result
    recommendation = result["recommendation"]

    assert recommendation["requires_manual_review"] is True
    assert "delet" not in recommendation["proposed_action"].lower()
    assert "precedent" in recommendation["citation"].lower()
    assert "policy-fin-07" in recommendation["citation"].lower() or \
           "orphaned-resources" in recommendation["citation"].lower()


def test_idle_gpu_case_produces_dollar_proposal_and_pauses(findings_by_resource):
    """Doesn't pin down which policy doc gets cited — the LLM's search
    query varies slightly run to run, so retrieval can land on either
    policy-fin-04 or policy-fin-11. Just checks for a real proposal:
    dollar figure, citation, paused at interrupt()."""
    finding = findings_by_resource["vm-ml-training-gpu-01"]
    _, result, _ = run_for_finding(finding, thread_id="test-idle-gpu")
    _print_case("Idle/cost-spike GPU case (vm-ml-training-gpu-01)", result)

    assert "__interrupt__" in result
    recommendation = result["recommendation"]

    assert recommendation["proposed_action"]
    assert recommendation["dollar_estimate"] > 0
    assert recommendation["citation"]
    assert ".md" in recommendation["citation"]


def test_resume_after_interrupt_completes_the_run(findings_by_resource):
    finding = findings_by_resource["vm-ml-training-gpu-01"]
    app, result, config = run_for_finding(finding, thread_id="test-resume")
    assert "__interrupt__" in result

    decision = {"approved": True, "approver": "test-user"}
    final_state = app.invoke(Command(resume=decision), config=config)

    print(f"\n=== Resumed run final state ===")
    print(f"human_decision: {final_state['human_decision']}")

    assert "__interrupt__" not in final_state
    assert final_state["human_decision"] == decision
    assert final_state["recommendation"] == result["recommendation"]
