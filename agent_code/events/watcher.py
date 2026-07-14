"""
Bridges detector.py's findings to Kafka. Only categories with a real
utilization signal (oversized_or_idle_compute, cost_spike) go through
this path — the Kafka trigger is a utilization breach, not a billing
event. The other categories (orphaned_disk, orphaned_static_ip,
storage_lifecycle_candidate) aren't published here.
"""

import csv
import os

from detector import DATA_DIR, Finding, run_detector
from events.producer import AnomalyEventProducer, BillingAnomalyEvent, make_event

UTILIZATION_SIGNAL_CATEGORIES = {"oversized_or_idle_compute", "cost_spike"}


def load_resource_billing_facts(billing_csv_path: str) -> dict:
    """resource_name -> {"team", "project_id", "daily_cost_usd"}, taken
    from that resource's most recent row in billing_export.csv."""
    facts = {}
    with open(billing_csv_path, newline="") as f:
        for row in csv.DictReader(f):
            facts[row["resource_name"]] = {
                "team": row["team"],
                "project_id": row["project_id"],
                "daily_cost_usd": float(row["cost_usd"]),
            }
    return facts


def build_breach_events(findings: list[Finding], billing_facts: dict) -> list[BillingAnomalyEvent]:
    events = []
    for finding in findings:
        if finding.category not in UTILIZATION_SIGNAL_CATEGORIES:
            continue
        facts = billing_facts[finding.resource_name]
        events.append(make_event(
            resource_name=finding.resource_name,
            team=facts["team"],
            project_id=facts["project_id"],
            anomaly_type=finding.category,
            daily_cost_usd=facts["daily_cost_usd"],
            detail={"reason": finding.detail},
        ))
    return events


def watch_and_publish(producer: AnomalyEventProducer, data_dir: str = DATA_DIR) -> list[BillingAnomalyEvent]:
    findings = run_detector(data_dir)
    billing_facts = load_resource_billing_facts(os.path.join(data_dir, "billing_export.csv"))
    events = build_breach_events(findings, billing_facts)

    for event in events:
        producer.publish(event)
    producer.flush()
    return events


def main():
    # Not yet confirmed against a live install — see infra/kafka/README.md.
    producer = AnomalyEventProducer(
        bootstrap_servers="finops-kafka.finops-copilot.svc.cluster.local:9092"
    )
    events = watch_and_publish(producer)
    print(f"Published {len(events)} utilization-breach events")


if __name__ == "__main__":
    main()
