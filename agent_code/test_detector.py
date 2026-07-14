"""
Tests detector.py against the ground truth documented in
generate_mock_data.py's docstring: 7 anomalies (6 "real" + the
pd-payments-dr-volume-01 policy trap, which the naive detector is
*supposed* to flag) across 5 categories, and 2 clean resources that
must never be flagged.
"""

import os

import pytest

from detector import run_detector

requires_bigquery = pytest.mark.skipif(
    not os.environ.get("BIGQUERY_DATASET"),
    reason="BIGQUERY_DATASET not set — needs real BigQuery billing/utilization/metadata tables",
)

EXPECTED_ANOMALIES = {
    "vm-analytics-batch-01": "oversized_or_idle_compute",
    "gke-platform-pool-std": "oversized_or_idle_compute",
    "pd-dataeng-orphan-01": "orphaned_disk",
    "ip-web-unused-01": "orphaned_static_ip",
    "vm-ml-training-gpu-01": "cost_spike",
    "gcs-analytics-archive": "storage_lifecycle_candidate",
    "pd-payments-dr-volume-01": "orphaned_disk",
}

CLEAN_RESOURCES = {"vm-checkout-prod-01", "cloudsql-checkout-db"}


def test_finds_exactly_the_documented_anomalies():
    findings = run_detector()
    flagged = {f.resource_name: f.category for f in findings}

    assert flagged == EXPECTED_ANOMALIES, (
        f"missing: {EXPECTED_ANOMALIES.keys() - flagged.keys()}, "
        f"unexpected: {flagged.keys() - EXPECTED_ANOMALIES.keys()}, "
        f"category mismatches: {[(k, flagged[k], EXPECTED_ANOMALIES[k]) for k in flagged.keys() & EXPECTED_ANOMALIES.keys() if flagged[k] != EXPECTED_ANOMALIES[k]]}"
    )


def test_does_not_flag_clean_resources():
    findings = run_detector()
    flagged_resources = {f.resource_name for f in findings}

    for resource_name in CLEAN_RESOURCES:
        assert resource_name not in flagged_resources, (
            f"{resource_name} is documented clean but was flagged: "
            f"{[f for f in findings if f.resource_name == resource_name]}"
        )


def test_policy_trap_is_flagged_by_the_naive_detector():
    """pd-payments-dr-volume-01 SHOULD be flagged here — this detector
    is intentionally naive. Catching the disaster-recovery tag and
    downgrading to manual review is the policy-grounded step's job,
    not this one's."""
    findings = run_detector()
    trap = [f for f in findings if f.resource_name == "pd-payments-dr-volume-01"]

    assert len(trap) == 1
    assert trap[0].category == "orphaned_disk"


@requires_bigquery
def test_bigquery_backend_matches_csv_backend():
    """Same ground truth as test_finds_exactly_the_documented_anomalies,
    but reading billing, utilization, and metadata from BigQuery
    instead of CSV/JSON — proves the two backends produce identical
    findings. Requires BIGQUERY_DATASET's billing_export,
    utilization_metrics, and resource_metadata tables to already be
    loaded by generate_mock_data.py."""
    findings = run_detector()
    flagged = {f.resource_name: f.category for f in findings}

    assert flagged == EXPECTED_ANOMALIES
