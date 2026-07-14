import json
from unittest.mock import MagicMock, patch

from events.consumer_dashboard_counter import DashboardCounterConsumer
from events.consumer_orchestrator import OrchestratorConsumer


def _mock_message(value: dict):
    msg = MagicMock()
    msg.error.return_value = None
    msg.value.return_value = json.dumps(value).encode("utf-8")
    return msg


@patch("events.consumer_orchestrator.Consumer")
def test_orchestrator_consumer_subscribes_to_billing_anomaly_topic(mock_consumer_cls):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer

    OrchestratorConsumer(bootstrap_servers="dummy:9092")

    mock_consumer.subscribe.assert_called_once_with(["billing.anomaly.detected"])


EVENT = {
    "resource_name": "vm-analytics-batch-01",
    "anomaly_type": "oversized_or_idle_compute",
    "detail": {"reason": "avg CPU 4.0% over 14 days"},
    "detected_at_epoch": 1_700_000_000,
}


@patch("events.consumer_orchestrator.approvals.redis_client")
@patch("events.consumer_orchestrator.approvals.create_token")
@patch("events.consumer_orchestrator.orchestrator.run_for_finding")
@patch("events.consumer_orchestrator.Consumer")
def test_orchestrator_consumer_runs_real_pipeline_and_creates_token(
    mock_consumer_cls, mock_run_for_finding, mock_create_token, mock_redis_client, capsys
):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer
    mock_consumer.poll.return_value = _mock_message(EVENT)
    mock_redis_client.return_value.exists.return_value = 0  # not seen before
    recommendation = {"proposed_action": "flag for manual review", "dollar_estimate": 10.0}
    mock_run_for_finding.return_value = (MagicMock(), {"recommendation": recommendation}, MagicMock())
    mock_create_token.return_value = "fake-token-123"

    consumer = OrchestratorConsumer(bootstrap_servers="dummy:9092")
    result = consumer.poll_once()

    assert result == {
        "resource_name": "vm-analytics-batch-01",
        "recommendation": recommendation,
        "approval_token": "fake-token-123",
    }

    # Finding passed to the real orchestrator is built from the event's
    # own fields, not a fresh detector.run_detector() call.
    finding_arg = mock_run_for_finding.call_args.args[0]
    assert finding_arg.resource_name == "vm-analytics-batch-01"
    assert finding_arg.category == "oversized_or_idle_compute"
    assert finding_arg.detail == "avg CPU 4.0% over 14 days"

    # Marked processed (dedup guard) and committed (manual offset
    # commit), both only after the pipeline actually succeeded.
    mock_redis_client.return_value.setex.assert_called_once()
    dedup_key = mock_redis_client.return_value.setex.call_args.args[0]
    assert dedup_key == "finops:kafka-processed:vm-analytics-batch-01:oversized_or_idle_compute:1700000000"
    mock_consumer.commit.assert_called_once_with(mock_consumer.poll.return_value)

    captured = capsys.readouterr()
    assert "proposal ready" in captured.out
    assert "vm-analytics-batch-01" in captured.out


@patch("events.consumer_orchestrator.approvals.redis_client")
@patch("events.consumer_orchestrator.approvals.create_token")
@patch("events.consumer_orchestrator.orchestrator.run_for_finding")
@patch("events.consumer_orchestrator.Consumer")
def test_orchestrator_consumer_skips_duplicate_delivery(
    mock_consumer_cls, mock_run_for_finding, mock_create_token, mock_redis_client, capsys
):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer
    mock_consumer.poll.return_value = _mock_message(EVENT)
    mock_redis_client.return_value.exists.return_value = 1  # already processed

    consumer = OrchestratorConsumer(bootstrap_servers="dummy:9092")
    result = consumer.poll_once()

    assert result is None
    mock_run_for_finding.assert_not_called()
    mock_create_token.assert_not_called()
    # Still commits — this delivery is intentionally skipped, not
    # deferred, so the offset has to advance past it either way.
    mock_consumer.commit.assert_called_once_with(mock_consumer.poll.return_value)

    captured = capsys.readouterr()
    assert "duplicate delivery, skipping" in captured.out


@patch("events.consumer_orchestrator.approvals.redis_client")
@patch("events.consumer_orchestrator.approvals.create_token")
@patch("events.consumer_orchestrator.orchestrator.run_for_finding")
@patch("events.consumer_orchestrator.Consumer")
def test_orchestrator_consumer_does_not_commit_on_pipeline_failure(
    mock_consumer_cls, mock_run_for_finding, mock_create_token, mock_redis_client
):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer
    mock_consumer.poll.return_value = _mock_message(EVENT)
    mock_redis_client.return_value.exists.return_value = 0
    mock_run_for_finding.side_effect = RuntimeError("OpenAI API error")

    consumer = OrchestratorConsumer(bootstrap_servers="dummy:9092")
    try:
        consumer.poll_once()
        assert False, "expected the pipeline failure to propagate"
    except RuntimeError:
        pass

    # A failed pipeline must leave the message uncommitted and
    # unmarked, so it gets redelivered and retried instead of lost.
    mock_redis_client.return_value.setex.assert_not_called()
    mock_consumer.commit.assert_not_called()


@patch("events.consumer_orchestrator.Consumer")
def test_orchestrator_consumer_returns_none_on_empty_poll(mock_consumer_cls):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer
    mock_consumer.poll.return_value = None

    consumer = OrchestratorConsumer(bootstrap_servers="dummy:9092")

    assert consumer.poll_once() is None


@patch("events.consumer_orchestrator.Consumer")
def test_orchestrator_consumer_returns_none_on_consumer_error(mock_consumer_cls):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer
    error_msg = MagicMock()
    error_msg.error.return_value = "boom"
    mock_consumer.poll.return_value = error_msg

    consumer = OrchestratorConsumer(bootstrap_servers="dummy:9092")

    assert consumer.poll_once() is None


@patch("events.consumer_dashboard_counter.Consumer")
def test_dashboard_counter_subscribes_to_billing_anomaly_topic(mock_consumer_cls):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer

    DashboardCounterConsumer(bootstrap_servers="dummy:9092")

    mock_consumer.subscribe.assert_called_once_with(["billing.anomaly.detected"])


@patch("events.consumer_dashboard_counter.Consumer")
def test_dashboard_counter_increments_per_anomaly_type(mock_consumer_cls):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer

    events = [
        {"resource_name": "vm-a", "anomaly_type": "cost_spike"},
        {"resource_name": "vm-b", "anomaly_type": "cost_spike"},
        {"resource_name": "vm-c", "anomaly_type": "oversized_or_idle_compute"},
    ]
    mock_consumer.poll.side_effect = [_mock_message(e) for e in events]

    consumer = DashboardCounterConsumer(bootstrap_servers="dummy:9092")
    for _ in events:
        consumer.poll_once()

    assert consumer.counts_by_anomaly_type == {"cost_spike": 2, "oversized_or_idle_compute": 1}


@patch("events.consumer_dashboard_counter.Consumer")
def test_dashboard_counter_ignores_consumer_errors(mock_consumer_cls):
    mock_consumer = MagicMock()
    mock_consumer_cls.return_value = mock_consumer
    error_msg = MagicMock()
    error_msg.error.return_value = "boom"
    mock_consumer.poll.return_value = error_msg

    consumer = DashboardCounterConsumer(bootstrap_servers="dummy:9092")
    result = consumer.poll_once()

    assert result is None
    assert consumer.counts_by_anomaly_type == {}
