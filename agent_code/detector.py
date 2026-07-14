"""
Rule-based anomaly detector for the mock FinOps billing data.

Mechanical only — threshold and attachment-status checks, no policy
nuance, no LLM. It flags pd-payments-dr-volume-01 as an orphaned disk
even though that's a documented policy exception (INC-FIN-0033);
catching that and downgrading to manual review happens later, in
orchestrator.py.
"""

import csv
import json
import os
from collections import defaultdict
from dataclasses import dataclass

import metrics

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "billing")

CPU_IDLE_THRESHOLD_PCT = 20.0
COST_SPIKE_WINDOW_DAYS = 3
COST_SPIKE_RATIO_THRESHOLD = 1.5
STALE_STORAGE_DAYS = 90


@dataclass(frozen=True)
class Finding:
    resource_name: str
    category: str
    detail: str


def load_billing(path: str) -> dict:
    """resource_name -> list of (date, cost_usd, sku_description) sorted by date."""
    rows_by_resource = defaultdict(list)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows_by_resource[row["resource_name"]].append(
                (row["date"], float(row["cost_usd"]), row["sku_description"])
            )
    for rows in rows_by_resource.values():
        rows.sort(key=lambda r: r[0])
    return dict(rows_by_resource)


def load_billing_from_bigquery(table_id: str) -> dict:
    """Same return shape as load_billing(): resource_name -> list of
    (date, cost_usd, sku_description) sorted by date. table_id is a
    fully-qualified BigQuery table id (project.dataset.table)."""
    from google.cloud import bigquery

    client = bigquery.Client()
    query = f"SELECT date, resource_name, cost_usd, sku_description FROM `{table_id}`"

    rows_by_resource = defaultdict(list)
    for row in client.query(query).result():
        rows_by_resource[row.resource_name].append(
            (row.date.isoformat(), float(row.cost_usd), row.sku_description)
        )
    for rows in rows_by_resource.values():
        rows.sort(key=lambda r: r[0])
    return dict(rows_by_resource)


def load_utilization(path: str) -> dict:
    """resource_name -> list of avg_cpu_pct values."""
    cpu_by_resource = defaultdict(list)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            cpu_by_resource[row["resource_name"]].append(float(row["avg_cpu_pct"]))
    return dict(cpu_by_resource)


def load_utilization_from_bigquery(table_id: str) -> dict:
    """Same return shape as load_utilization(): resource_name -> list of
    avg_cpu_pct values, in date order."""
    from google.cloud import bigquery

    client = bigquery.Client()
    query = f"SELECT date, resource_name, avg_cpu_pct FROM `{table_id}` ORDER BY resource_name, date"

    cpu_by_resource = defaultdict(list)
    for row in client.query(query).result():
        cpu_by_resource[row.resource_name].append(float(row.avg_cpu_pct))
    return dict(cpu_by_resource)


def load_metadata(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def load_metadata_from_bigquery(table_id: str) -> dict:
    """Same return shape as load_metadata(): resource_name -> metadata
    dict. Resource-type-specific fields (machine_type, disk_type, etc.)
    are stored as a JSON string in the extra_attributes column and
    merged back in here."""
    from google.cloud import bigquery

    client = bigquery.Client()
    query = f"SELECT resource_name, team, attached_instance, last_accessed_days_ago, extra_attributes FROM `{table_id}`"

    metadata = {}
    for row in client.query(query).result():
        entry = {
            "team": row.team,
            "attached_instance": row.attached_instance,
            "last_accessed_days_ago": row.last_accessed_days_ago,
        }
        if row.extra_attributes:
            entry.update(json.loads(row.extra_attributes))
        metadata[row.resource_name] = entry
    return metadata


def detect_oversized_or_idle_compute(utilization: dict) -> list:
    findings = []
    for resource_name, cpu_values in utilization.items():
        avg_cpu = sum(cpu_values) / len(cpu_values)
        if avg_cpu < CPU_IDLE_THRESHOLD_PCT:
            findings.append(Finding(
                resource_name, "oversized_or_idle_compute",
                f"avg CPU {avg_cpu:.1f}% over {len(cpu_values)} days "
                f"(threshold {CPU_IDLE_THRESHOLD_PCT}%)",
            ))
    return findings


def detect_orphaned_disk(billing: dict, metadata: dict) -> list:
    findings = []
    for resource_name, rows in billing.items():
        sku_description = rows[0][2]
        meta = metadata.get(resource_name, {})
        if sku_description == "Storage PD Capacity" and meta.get("attached_instance") is False:
            findings.append(Finding(
                resource_name, "orphaned_disk",
                "persistent disk with no attached_instance in resource_metadata.json",
            ))
    return findings


def detect_orphaned_static_ip(billing: dict, metadata: dict) -> list:
    findings = []
    for resource_name, rows in billing.items():
        sku_description = rows[0][2]
        meta = metadata.get(resource_name, {})
        if sku_description == "Static IP Charge" and meta.get("attached_instance") is False:
            findings.append(Finding(
                resource_name, "orphaned_static_ip",
                "reserved static IP with no attached_instance in resource_metadata.json",
            ))
    return findings


def detect_cost_spike(billing: dict) -> list:
    findings = []
    for resource_name, rows in billing.items():
        if len(rows) <= COST_SPIKE_WINDOW_DAYS:
            continue
        costs = [c for _, c, _ in rows]
        recent = costs[-COST_SPIKE_WINDOW_DAYS:]
        prior = costs[:-COST_SPIKE_WINDOW_DAYS]
        recent_avg = sum(recent) / len(recent)
        prior_avg = sum(prior) / len(prior)
        if prior_avg > 0 and recent_avg / prior_avg >= COST_SPIKE_RATIO_THRESHOLD:
            findings.append(Finding(
                resource_name, "cost_spike",
                f"last {COST_SPIKE_WINDOW_DAYS}-day avg cost ${recent_avg:.2f} vs. prior "
                f"avg ${prior_avg:.2f} (ratio {recent_avg / prior_avg:.2f}x, "
                f"threshold {COST_SPIKE_RATIO_THRESHOLD}x)",
            ))
    return findings


def detect_storage_lifecycle_candidate(metadata: dict) -> list:
    findings = []
    for resource_name, meta in metadata.items():
        last_accessed = meta.get("last_accessed_days_ago")
        if last_accessed is not None and last_accessed > STALE_STORAGE_DAYS:
            findings.append(Finding(
                resource_name, "storage_lifecycle_candidate",
                f"last accessed {last_accessed} days ago (threshold {STALE_STORAGE_DAYS} days)",
            ))
    return findings


def run_detector(data_dir: str = DATA_DIR) -> list:
    """Runs every detect_* check and returns the combined findings.
    Findings are logged and counted in Prometheus here, since every
    caller (main(), orchestrator.py, events/watcher.py) goes through
    this function.

    All three of billing, utilization, and metadata come from BigQuery
    instead of the CSV/JSON files when BIGQUERY_DATASET (project.dataset)
    is set — reads from {BIGQUERY_DATASET}.billing_export,
    {BIGQUERY_DATASET}.utilization_metrics, and
    {BIGQUERY_DATASET}.resource_metadata. Unset (the default), all
    three fall back to the CSV/JSON files exactly as before."""
    bigquery_dataset = os.environ.get("BIGQUERY_DATASET")
    if bigquery_dataset:
        print(f"data source: BigQuery dataset {bigquery_dataset} "
              f"(billing_export, utilization_metrics, resource_metadata)")
        billing = load_billing_from_bigquery(f"{bigquery_dataset}.billing_export")
        utilization = load_utilization_from_bigquery(f"{bigquery_dataset}.utilization_metrics")
        metadata = load_metadata_from_bigquery(f"{bigquery_dataset}.resource_metadata")
    else:
        print(f"data source: local CSV/JSON in {data_dir}")
        billing = load_billing(os.path.join(data_dir, "billing_export.csv"))
        utilization = load_utilization(os.path.join(data_dir, "utilization_metrics.csv"))
        metadata = load_metadata(os.path.join(data_dir, "resource_metadata.json"))

    findings = []
    findings += detect_oversized_or_idle_compute(utilization)
    findings += detect_orphaned_disk(billing, metadata)
    findings += detect_orphaned_static_ip(billing, metadata)
    findings += detect_cost_spike(billing)
    findings += detect_storage_lifecycle_candidate(metadata)

    for f in findings:
        team = metadata.get(f.resource_name, {}).get("team", "unknown")
        print(f"anomaly detected: {f.resource_name:28s} {f.category:28s} {f.detail}")
        metrics.ANOMALIES_DETECTED.labels(anomaly_type=f.category, team=team).inc()

    return findings


def main():
    findings = run_detector()
    print(f"\n{len(findings)} findings total")


if __name__ == "__main__":
    main()
