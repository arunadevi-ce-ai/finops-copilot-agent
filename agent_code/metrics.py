"""
Prometheus metrics for the FinOps decision pipeline — what was found,
proposed, and resolved. No latency/error/pipeline-health metrics on
purpose: this measures the copilot's decisions, not its own uptime.

Every increment sits next to the matching log line (detector.py's
run_detector(), orchestrator.py's recommendation_agent/human_approval)
so the metric can't drift from the log.

`status` on finops_recommendations_total is proposed / approved /
rejected — no separate "manual_review" value. A recommendation
downgraded to manual review counts as rejected, since the pipeline is
refusing to auto-approve it, not waiting on a human decision.
"""

from prometheus_client import Counter, Gauge, start_http_server

ANOMALIES_DETECTED = Counter(
    "finops_anomalies_detected_total",
    "Anomalies found by detector.py, by category and owning team",
    ["anomaly_type", "team"],
)

PROJECTED_SAVINGS_DOLLARS = Counter(
    "finops_projected_savings_dollars_total",
    "Dollar amount named in each proposed recommendation, by category and owning team",
    ["anomaly_type", "team"],
)

RECOMMENDATIONS = Counter(
    "finops_recommendations_total",
    "Recommendations by outcome status and anomaly category",
    ["status", "anomaly_type"],
)

OPEN_RECOMMENDATIONS = Gauge(
    "finops_open_recommendations",
    "Recommendations awaiting resolution, by anomaly_type and team",
    ["anomaly_type", "team"],
)


def start_metrics_server(port: int = 8000) -> None:
    """Starts prometheus_client's built-in standalone HTTP server on
    /metrics. Used by orchestrator.py's demo entrypoint; api.py mounts
    metrics on its own ASGI app instead. Call once per process."""
    start_http_server(port)
