from unittest.mock import MagicMock, patch

from detector import Finding
from events.producer import AnomalyEventProducer
from events.watcher import (
    UTILIZATION_SIGNAL_CATEGORIES,
    build_breach_events,
    load_resource_billing_facts,
    watch_and_publish,
)


def test_load_resource_billing_facts_uses_latest_row(tmp_path):
    csv_path = tmp_path / "billing_export.csv"
    csv_path.write_text(
        "date,project_id,team,service,sku_description,resource_name,region,cost_usd\n"
        "2026-06-25,proj-x,team-x,Compute Engine,N2,vm-a,asia-southeast1,10.0\n"
        "2026-06-26,proj-x,team-x,Compute Engine,N2,vm-a,asia-southeast1,12.5\n"
    )

    facts = load_resource_billing_facts(str(csv_path))

    assert facts["vm-a"] == {"team": "team-x", "project_id": "proj-x", "daily_cost_usd": 12.5}


def test_build_breach_events_only_includes_utilization_signal_categories():
    findings = [
        Finding("vm-analytics-batch-01", "oversized_or_idle_compute", "avg CPU 4.0%"),
        Finding("pd-dataeng-orphan-01", "orphaned_disk", "no attached instance"),
        Finding("vm-ml-training-gpu-01", "cost_spike", "ratio 3.06x"),
    ]
    billing_facts = {
        "vm-analytics-batch-01": {"team": "analytics", "project_id": "proj-analytics-prod", "daily_cost_usd": 14.2},
        "pd-dataeng-orphan-01": {"team": "data-eng", "project_id": "proj-dataeng-prod", "daily_cost_usd": 3.1},
        "vm-ml-training-gpu-01": {"team": "ml-training", "project_id": "proj-mltraining-prod", "daily_cost_usd": 58.0},
    }

    events = build_breach_events(findings, billing_facts)

    assert {e.resource_name for e in events} == {"vm-analytics-batch-01", "vm-ml-training-gpu-01"}
    assert all(e.anomaly_type in UTILIZATION_SIGNAL_CATEGORIES for e in events)
    assert "pd-dataeng-orphan-01" not in {e.resource_name for e in events}


@patch("events.watcher.run_detector")
@patch("events.producer.Producer")
def test_watch_and_publish_publishes_and_flushes(mock_producer_cls, mock_run_detector, tmp_path):
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    billing_csv = tmp_path / "billing_export.csv"
    billing_csv.write_text(
        "date,project_id,team,service,sku_description,resource_name,region,cost_usd\n"
        "2026-06-25,proj-analytics-prod,analytics,Compute Engine,N2,vm-analytics-batch-01,asia-southeast1,14.2\n"
    )
    mock_run_detector.return_value = [
        Finding("vm-analytics-batch-01", "oversized_or_idle_compute", "avg CPU 4.0%"),
    ]

    producer = AnomalyEventProducer(bootstrap_servers="dummy:9092")
    events = watch_and_publish(producer, data_dir=str(tmp_path))

    assert len(events) == 1
    assert events[0].resource_name == "vm-analytics-batch-01"
    mock_producer.produce.assert_called_once()
    mock_producer.flush.assert_called_once()


@patch("events.watcher.run_detector")
@patch("events.producer.Producer")
def test_watch_and_publish_skips_non_utilization_findings(mock_producer_cls, mock_run_detector, tmp_path):
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    billing_csv = tmp_path / "billing_export.csv"
    billing_csv.write_text(
        "date,project_id,team,service,sku_description,resource_name,region,cost_usd\n"
        "2026-06-25,proj-dataeng-prod,data-eng,Compute Engine,Storage PD Capacity,pd-dataeng-orphan-01,asia-southeast1,3.1\n"
    )
    mock_run_detector.return_value = [
        Finding("pd-dataeng-orphan-01", "orphaned_disk", "no attached instance"),
    ]

    producer = AnomalyEventProducer(bootstrap_servers="dummy:9092")
    events = watch_and_publish(producer, data_dir=str(tmp_path))

    assert events == []
    mock_producer.produce.assert_not_called()
    mock_producer.flush.assert_called_once()
