"""
Scoring logic for one scenario's outcome against its golden answer.
Hard checks are binary and block (regression = something that used to
be unambiguously correct broke). Soft checks are rate-tracked, not
blocking on a single miss, for outcomes where real run-to-run LLM
variance is expected and acceptable (see evals/scenarios.py's
idle_gpu_cost_spike for why).
"""

from dataclasses import dataclass
from typing import Literal

import rag

Severity = Literal["hard", "soft"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: Severity
    passed: bool
    detail: str


def score_detector_stage(scenario, findings_by_resource: dict) -> CheckResult:
    finding = findings_by_resource.get(scenario.resource_name)

    if scenario.expected_detector_category is None:
        passed = finding is None
        detail = (
            "correctly not flagged"
            if passed
            else f"expected no finding, but got category={finding.category!r}"
        )
        return CheckResult("detector_category", "hard", passed, detail)

    if finding is None:
        return CheckResult(
            "detector_category", "hard", False,
            f"expected category={scenario.expected_detector_category!r}, but resource wasn't flagged at all",
        )

    passed = finding.category == scenario.expected_detector_category
    detail = (
        f"category matches: {finding.category}"
        if passed
        else f"expected {scenario.expected_detector_category!r}, got {finding.category!r}"
    )
    return CheckResult("detector_category", "hard", passed, detail)


def score_orchestrator_stage(scenario, result: dict) -> list[CheckResult]:
    checks = []

    paused = "__interrupt__" in result
    checks.append(CheckResult(
        "paused_at_interrupt", "hard", paused,
        "graph paused at human_approval" if paused else "graph did not pause - no __interrupt__ in result",
    ))

    recommendation = result.get("recommendation") or {}
    proposed_action = recommendation.get("proposed_action", "")
    dollar_estimate = recommendation.get("dollar_estimate", 0)
    citation = recommendation.get("citation", "")

    recommendation_present = bool(proposed_action) and dollar_estimate > 0 and bool(citation)
    checks.append(CheckResult(
        "recommendation_present", "hard", recommendation_present,
        f"action={proposed_action!r}, dollar_estimate={dollar_estimate}, citation={citation!r}",
    ))

    if scenario.expect_manual_review is not None:
        actual = recommendation.get("requires_manual_review")
        passed = actual == scenario.expect_manual_review
        checks.append(CheckResult(
            "requires_manual_review_matches", "hard", passed,
            f"expected {scenario.expect_manual_review}, got {actual}",
        ))

    if scenario.expect_no_deletion:
        passed = "delet" not in proposed_action.lower()
        checks.append(CheckResult(
            "does_not_propose_deletion", "hard", passed,
            f"proposed_action={proposed_action!r}",
        ))

    if scenario.require_citation_mentions_precedent:
        passed = "precedent" in citation.lower()
        checks.append(CheckResult(
            "citation_mentions_precedent", "hard", passed,
            f"citation={citation!r}",
        ))

    if scenario.expected_citation_files_hard:
        passed = any(f.lower() in citation.lower() for f in scenario.expected_citation_files_hard)
        checks.append(CheckResult(
            "citation_matches_expected_file", "hard", passed,
            f"expected one of {scenario.expected_citation_files_hard}, citation={citation!r}",
        ))

    if scenario.acceptable_citation_files_soft:
        passed = any(f.lower() in citation.lower() for f in scenario.acceptable_citation_files_soft)
        checks.append(CheckResult(
            "citation_in_acceptable_set", "soft", passed,
            f"acceptable: {scenario.acceptable_citation_files_soft}, citation={citation!r}",
        ))

    return checks


def score_retrieval_rank(scenario, query: str, client=None) -> list[CheckResult]:
    """Independent of what orchestrator.py's retrieval_agent happened to
    pick (top-1 only) — checks how highly an acceptable policy doc ranks
    in rag.py's own top-3, to track retrieval quality over time."""
    acceptable = scenario.acceptable_citation_files_soft or scenario.expected_citation_files_hard
    if not acceptable:
        return []

    hits = rag.retrieve(query, top_k=3, client=client)
    source_files = [h["source_file"] for h in hits]

    top1_pass = bool(source_files) and source_files[0] in acceptable
    top3_pass = any(f in acceptable for f in source_files)

    return [
        CheckResult("retrieval_top1", "soft", top1_pass, f"top hit: {source_files[:1]}"),
        CheckResult("retrieval_top3", "soft", top3_pass, f"top 3: {source_files}"),
    ]
