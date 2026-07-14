"""
Generates three files under data/billing/:

  - billing_export.csv      — daily cost per resource, shaped like a GCP
                               Billing Export table.
  - utilization_metrics.csv — daily avg CPU/mem for compute resources.
                               Kept separate from billing on purpose:
                               cost alone can't tell you a resource is
                               idle, only that it's expensive.
  - resource_metadata.json  — attachment status and last-access facts
                               billing data doesn't carry (would come
                               from Cloud Asset Inventory in reality).

GROUND TRUTH — detector.py and test_detector.py are tested against
this. Don't change resource names or scenarios without updating both.

  Anomalous:
    vm-analytics-batch-01   -> oversized_or_idle_compute (16 vCPU, ~4% CPU)
    gke-platform-pool-std   -> oversized_or_idle_compute (fixed 6-node pool, ~15% CPU)
    pd-dataeng-orphan-01    -> orphaned_disk (not attached to any instance)
    ip-web-unused-01        -> orphaned_static_ip (reserved, not attached)
    vm-ml-training-gpu-01   -> cost_spike (left running 24/7 for the last 3
                                days instead of its normal ~8h/day pattern)
    gcs-analytics-archive   -> storage_lifecycle_candidate (last accessed
                                140 days ago, still on standard storage)

  Clean (must NOT be flagged):
    vm-checkout-prod-01     -> healthy, ~65% CPU, no metadata red flags
    cloudsql-checkout-db    -> healthy, steady cost, well-utilized

  Policy trap (naive detector false positive):
    pd-payments-dr-volume-01 -> looks identical to pd-dataeng-orphan-01
                                (orphaned, no snapshot schedule, ~$3-4/day),
                                so a rule-based detector will flag it for
                                deletion. That's wrong: it's tagged
                                purpose=disaster-recovery, matching the
                                INC-FIN-0033 precedent in
                                policy-fin-07-orphaned-resources.md. The
                                policy-grounded step has to catch the tag
                                and downgrade this to manual review.
"""

import csv
import json
import os
import random
from datetime import date, timedelta

random.seed(42)

OUT_DIR = os.path.join(os.path.dirname(__file__), "data", "billing")
NUM_DAYS = 14
START_DATE = date(2026, 6, 25)

RESOURCES = [
    {
        "resource_name": "vm-analytics-batch-01", "team": "analytics",
        "service": "Compute Engine", "sku_description": "N2 Instance Core running in Singapore",
        "region": "asia-southeast1", "daily_cost": 14.20, "is_compute": True,
        "cpu_pattern": lambda d: 4 + random.uniform(-1, 1),
        "mem_pattern": lambda d: 8 + random.uniform(-2, 2),
    },
    {
        "resource_name": "gke-platform-pool-std", "team": "platform",
        "service": "Kubernetes Engine", "sku_description": "N2 Instance Core running in Singapore",
        "region": "asia-southeast1", "daily_cost": 41.60, "is_compute": True,
        "cpu_pattern": lambda d: 15 + random.uniform(-3, 3),
        "mem_pattern": lambda d: 22 + random.uniform(-3, 3),
    },
    {
        "resource_name": "pd-dataeng-orphan-01", "team": "data-eng",
        "service": "Compute Engine", "sku_description": "Storage PD Capacity",
        "region": "asia-southeast1", "daily_cost": 3.10, "is_compute": False,
    },
    {
        "resource_name": "ip-web-unused-01", "team": "web",
        "service": "Compute Engine", "sku_description": "Static IP Charge",
        "region": "asia-southeast1", "daily_cost": 0.75, "is_compute": False,
    },
    {
        "resource_name": "vm-ml-training-gpu-01", "team": "ml-training",
        "service": "Compute Engine", "sku_description": "A2 GPU Instance running in Singapore",
        "region": "asia-southeast1", "daily_cost": None, "is_compute": True,
        # Cost/utilization set per-day below to encode the spike explicitly.
    },
    {
        "resource_name": "gcs-analytics-archive", "team": "analytics",
        "service": "Cloud Storage", "sku_description": "Standard Storage Singapore",
        "region": "asia-southeast1", "daily_cost": 2.05, "is_compute": False,
    },
    {
        "resource_name": "vm-checkout-prod-01", "team": "checkout",
        "service": "Compute Engine", "sku_description": "N2 Instance Core running in Singapore",
        "region": "asia-southeast1", "daily_cost": 8.40, "is_compute": True,
        "cpu_pattern": lambda d: 65 + random.uniform(-8, 8),
        "mem_pattern": lambda d: 58 + random.uniform(-6, 6),
    },
    {
        "resource_name": "cloudsql-checkout-db", "team": "checkout",
        "service": "Cloud SQL", "sku_description": "DB Standard Instance Singapore",
        "region": "asia-southeast1", "daily_cost": 25.30, "is_compute": True,
        "cpu_pattern": lambda d: 55 + random.uniform(-10, 10),
        "mem_pattern": lambda d: 70 + random.uniform(-5, 5),
    },
    {
        "resource_name": "pd-payments-dr-volume-01", "team": "payments",
        "service": "Compute Engine", "sku_description": "Storage PD Capacity",
        "region": "asia-southeast1", "daily_cost": 3.65, "is_compute": False,
    },
]

PROJECT_ID_BY_TEAM = {
    "analytics": "proj-analytics-prod", "platform": "proj-platform-prod",
    "data-eng": "proj-dataeng-prod", "web": "proj-web-prod",
    "ml-training": "proj-mltraining-prod", "checkout": "proj-checkout-prod",
    "payments": "proj-payments-prod",
}


def push_billing_to_bigquery(billing_rows: list[dict], table_id: str) -> None:
    """Loads billing_rows into a BigQuery table, replacing its contents
    each run. table_id is a fully-qualified BigQuery table id
    (project.dataset.table); the dataset and table must already exist."""
    from google.cloud import bigquery

    client = bigquery.Client()
    schema = [
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("project_id", "STRING"),
        bigquery.SchemaField("team", "STRING"),
        bigquery.SchemaField("service", "STRING"),
        bigquery.SchemaField("sku_description", "STRING"),
        bigquery.SchemaField("resource_name", "STRING"),
        bigquery.SchemaField("region", "STRING"),
        bigquery.SchemaField("cost_usd", "FLOAT64"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client.load_table_from_json(billing_rows, table_id, job_config=job_config).result()


def push_utilization_to_bigquery(util_rows: list[dict], table_id: str) -> None:
    """Loads util_rows into a BigQuery table, replacing its contents
    each run."""
    from google.cloud import bigquery

    client = bigquery.Client()
    schema = [
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("resource_name", "STRING"),
        bigquery.SchemaField("avg_cpu_pct", "FLOAT64"),
        bigquery.SchemaField("avg_mem_pct", "FLOAT64"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client.load_table_from_json(util_rows, table_id, job_config=job_config).result()


def push_metadata_to_bigquery(metadata: dict, table_id: str) -> None:
    """Loads metadata into a BigQuery table, replacing its contents each
    run. Resource-type-specific fields (machine_type, disk_type, etc.)
    get packed into a JSON string column, since they vary by resource
    and a fixed flat schema can't hold all of them as real columns."""
    from google.cloud import bigquery

    client = bigquery.Client()
    schema = [
        bigquery.SchemaField("resource_name", "STRING"),
        bigquery.SchemaField("team", "STRING"),
        bigquery.SchemaField("attached_instance", "BOOL"),
        bigquery.SchemaField("last_accessed_days_ago", "INT64"),
        bigquery.SchemaField("extra_attributes", "STRING"),
    ]
    core_fields = {"team", "attached_instance", "last_accessed_days_ago"}
    rows = []
    for resource_name, meta in metadata.items():
        extra = {k: v for k, v in meta.items() if k not in core_fields}
        rows.append({
            "resource_name": resource_name,
            "team": meta.get("team"),
            "attached_instance": meta.get("attached_instance"),
            "last_accessed_days_ago": meta.get("last_accessed_days_ago"),
            "extra_attributes": json.dumps(extra) if extra else None,
        })
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result()


def gpu_cost_and_util(day_index: int) -> tuple[float, float]:
    """Normal pattern: ~8h/day utilization, cost reflects partial-day billing.
    Last 3 days: instance left running 24/7 (utilization crashes overnight
    but billing stays high all day) — the cost_spike scenario."""
    if day_index >= NUM_DAYS - 3:
        cost = 58.00 + random.uniform(-2, 2)        # billed as if running all day
        cpu = 6 + random.uniform(-2, 2)              # but barely used -> idle-while-running
    else:
        cost = 19.50 + random.uniform(-1.5, 1.5)      # ~8h/day billing pattern
        cpu = 78 + random.uniform(-8, 8)              # busy while it's on
    return round(cost, 2), round(cpu, 1)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    billing_rows, util_rows = [], []

    for day_index in range(NUM_DAYS):
        day = START_DATE + timedelta(days=day_index)
        for r in RESOURCES:
            if r["resource_name"] == "vm-ml-training-gpu-01":
                cost, cpu = gpu_cost_and_util(day_index)
                mem = round(cpu * 0.9 + random.uniform(-2, 2), 1)
            else:
                cost = round(r["daily_cost"] + random.uniform(-0.3, 0.3), 2)
                cpu = round(r["cpu_pattern"](day), 1) if r.get("is_compute") else None
                mem = round(r["mem_pattern"](day), 1) if r.get("is_compute") else None

            billing_rows.append({
                "date": day.isoformat(),
                "project_id": PROJECT_ID_BY_TEAM[r["team"]],
                "team": r["team"],
                "service": r["service"],
                "sku_description": r["sku_description"],
                "resource_name": r["resource_name"],
                "region": r["region"],
                "cost_usd": cost,
            })
            if r.get("is_compute"):
                util_rows.append({
                    "date": day.isoformat(),
                    "resource_name": r["resource_name"],
                    "avg_cpu_pct": cpu,
                    "avg_mem_pct": mem,
                })

    with open(os.path.join(OUT_DIR, "billing_export.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(billing_rows[0].keys()))
        writer.writeheader()
        writer.writerows(billing_rows)

    with open(os.path.join(OUT_DIR, "utilization_metrics.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(util_rows[0].keys()))
        writer.writeheader()
        writer.writerows(util_rows)

    metadata = {
        "vm-analytics-batch-01": {"team": "analytics", "machine_type": "n2-standard-16", "attached_instance": True, "last_accessed_days_ago": None},
        "gke-platform-pool-std": {"team": "platform", "machine_type": "n2-standard-8 x6 (fixed)", "attached_instance": True, "last_accessed_days_ago": None, "autoscaling_enabled": False},
        "pd-dataeng-orphan-01": {"team": "data-eng", "disk_type": "pd-ssd", "size_gb": 500, "attached_instance": False, "last_accessed_days_ago": None},
        "ip-web-unused-01": {"team": "web", "attached_instance": False, "last_accessed_days_ago": None},
        "vm-ml-training-gpu-01": {"team": "ml-training", "machine_type": "a2-highgpu-1g", "attached_instance": True, "last_accessed_days_ago": None},
        "gcs-analytics-archive": {"team": "analytics", "storage_class": "STANDARD", "attached_instance": None, "last_accessed_days_ago": 140},
        "vm-checkout-prod-01": {"team": "checkout", "machine_type": "n2-standard-4", "attached_instance": True, "last_accessed_days_ago": None},
        "cloudsql-checkout-db": {"team": "checkout", "tier": "db-standard-2", "attached_instance": True, "last_accessed_days_ago": None},
        "pd-payments-dr-volume-01": {"team": "payments", "disk_type": "pd-ssd", "size_gb": 400, "attached_instance": False, "last_accessed_days_ago": None, "purpose": "disaster-recovery"},
    }
    with open(os.path.join(OUT_DIR, "resource_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"Wrote {len(billing_rows)} billing rows, {len(util_rows)} utilization rows, "
          f"{len(metadata)} resource metadata entries to {OUT_DIR}")

    bigquery_dataset = os.environ.get("BIGQUERY_DATASET")
    if bigquery_dataset:
        push_billing_to_bigquery(billing_rows, f"{bigquery_dataset}.billing_export")
        push_utilization_to_bigquery(util_rows, f"{bigquery_dataset}.utilization_metrics")
        push_metadata_to_bigquery(metadata, f"{bigquery_dataset}.resource_metadata")
        print(f"Loaded billing, utilization, and metadata into BigQuery dataset {bigquery_dataset}")


if __name__ == "__main__":
    main()
