"""
Confirms Prometheus metrics increment correctly for the DR-volume trap
case, end to end: detected -> investigated -> proposed -> downgraded to
manual review.

Runs the real detector and orchestrator graph (real OpenAI calls).
Reads metric deltas rather than absolute values, since
prometheus_client's registry is shared global state across the whole
test session.
"""

import detector
import metrics
from orchestrator import run_for_finding

RESOURCE_NAME = "pd-payments-dr-volume-01"
ANOMALY_TYPE = "orphaned_disk"
TEAM = "payments"


def _value(metric, **labels):
    child = metric.labels(**labels) if labels else metric
    return child._value.get()


def test_dr_volume_trap_metrics_end_to_end():
    before = {
        "detected": _value(metrics.ANOMALIES_DETECTED, anomaly_type=ANOMALY_TYPE, team=TEAM),
        "proposed": _value(metrics.RECOMMENDATIONS, status="proposed", anomaly_type=ANOMALY_TYPE),
        "rejected": _value(metrics.RECOMMENDATIONS, status="rejected", anomaly_type=ANOMALY_TYPE),
        "approved": _value(metrics.RECOMMENDATIONS, status="approved", anomaly_type=ANOMALY_TYPE),
        "savings": _value(metrics.PROJECTED_SAVINGS_DOLLARS, anomaly_type=ANOMALY_TYPE, team=TEAM),
        "open": _value(metrics.OPEN_RECOMMENDATIONS, anomaly_type=ANOMALY_TYPE, team=TEAM),
    }

    findings_by_resource = {f.resource_name: f for f in detector.run_detector()}
    finding = findings_by_resource[RESOURCE_NAME]
    assert finding.category == ANOMALY_TYPE  # sanity: still the trap case detector.py documents

    _, result, _ = run_for_finding(finding, thread_id="test-metrics-dr-volume")
    recommendation = result["recommendation"]
    assert recommendation["requires_manual_review"] is True  # sanity: still downgraded

    after = {
        "detected": _value(metrics.ANOMALIES_DETECTED, anomaly_type=ANOMALY_TYPE, team=TEAM),
        "proposed": _value(metrics.RECOMMENDATIONS, status="proposed", anomaly_type=ANOMALY_TYPE),
        "rejected": _value(metrics.RECOMMENDATIONS, status="rejected", anomaly_type=ANOMALY_TYPE),
        "approved": _value(metrics.RECOMMENDATIONS, status="approved", anomaly_type=ANOMALY_TYPE),
        "savings": _value(metrics.PROJECTED_SAVINGS_DOLLARS, anomaly_type=ANOMALY_TYPE, team=TEAM),
        "open": _value(metrics.OPEN_RECOMMENDATIONS, anomaly_type=ANOMALY_TYPE, team=TEAM),
    }

    delta = {k: after[k] - before[k] for k in before}
    print(f"\nMetric deltas for {RESOURCE_NAME}:")
    for k, v in delta.items():
        print(f"  {k}: {v:+}")

    assert delta["detected"] == 1, "finops_anomalies_detected_total didn't increment"
    assert delta["proposed"] == 1, "finops_recommendations_total{status=proposed} didn't increment"
    assert delta["rejected"] == 1, (
        "finops_recommendations_total{status=rejected} didn't increment — the manual-review "
        "downgrade must show up as something beyond just 'proposed'"
    )
    assert delta["approved"] == 0, "the DR-volume trap case must never be auto-approved"
    assert delta["savings"] == recommendation["dollar_estimate"]
    assert delta["open"] == 0, "opened by the proposal, then immediately closed by the auto-reject-to-manual-review path"
