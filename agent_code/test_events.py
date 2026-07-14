import json
from unittest.mock import MagicMock, patch

from events.producer import AnomalyEventProducer, TOPIC, make_event


def test_make_event_sets_expected_fields():
    event = make_event(
        resource_name="vm-ml-training-gpu-01", team="ml-training",
        project_id="proj-mltraining-prod", anomaly_type="cost_spike",
        daily_cost_usd=58.09, detail={"avg_cpu_pct": 6.3, "previous_avg_cpu_pct": 78.0},
    )
    assert event.resource_name == "vm-ml-training-gpu-01"
    assert event.anomaly_type == "cost_spike"
    assert event.detected_at_epoch > 0


def test_event_to_json_round_trips():
    event = make_event(
        resource_name="pd-dataeng-orphan-01", team="data-eng",
        project_id="proj-dataeng-prod", anomaly_type="orphaned_disk",
        daily_cost_usd=3.10, detail={"attached_instance": False},
    )
    parsed = json.loads(event.to_json())
    assert parsed["resource_name"] == "pd-dataeng-orphan-01"
    assert parsed["detail"]["attached_instance"] is False


@patch("events.producer.Producer")
def test_publish_calls_produce_with_correct_topic_and_key(mock_producer_cls):
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    event = make_event(
        resource_name="ip-web-unused-01", team="web", project_id="proj-web-prod",
        anomaly_type="orphaned_static_ip", daily_cost_usd=0.75, detail={},
    )
    producer = AnomalyEventProducer(bootstrap_servers="finops-kafka-kafka-bootstrap:9092")
    producer.publish(event)

    mock_producer.produce.assert_called_once()
    _, kwargs = mock_producer.produce.call_args
    assert kwargs["topic"] == TOPIC
    assert kwargs["key"] == "ip-web-unused-01"
    assert json.loads(kwargs["value"])["anomaly_type"] == "orphaned_static_ip"


@patch("events.producer.Producer")
def test_publish_triggers_poll_for_delivery_callbacks(mock_producer_cls):
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    event = make_event(
        resource_name="gcs-analytics-archive", team="analytics", project_id="proj-analytics-prod",
        anomaly_type="storage_lifecycle_candidate", daily_cost_usd=2.05, detail={"last_accessed_days_ago": 140},
    )
    producer = AnomalyEventProducer(bootstrap_servers="finops-kafka-kafka-bootstrap:9092")
    producer.publish(event)

    mock_producer.poll.assert_called_once_with(0)


@patch("events.producer.Producer")
def test_flush_delegates_to_underlying_producer_with_timeout(mock_producer_cls):
    mock_producer = MagicMock()
    mock_producer.flush.return_value = 0
    mock_producer_cls.return_value = mock_producer

    producer = AnomalyEventProducer(bootstrap_servers="finops-kafka-kafka-bootstrap:9092")
    remaining = producer.flush(timeout=5.0)

    mock_producer.flush.assert_called_once_with(5.0)
    assert remaining == 0
