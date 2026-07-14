"""
Stub consumer for the dashboard-counter side of the billing.anomaly.detected
fan-out. Uses a plain dict for now — swapping in a real metrics.py
Counter is a small change once this consumer is actually wired up to
run continuously.
"""

import json
from collections import defaultdict

from confluent_kafka import Consumer

from events.producer import TOPIC


class DashboardCounterConsumer:
    def __init__(self, bootstrap_servers: str, group_id: str = "finops-dashboard-counter"):
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
        })
        self._consumer.subscribe([TOPIC])
        self.counts_by_anomaly_type = defaultdict(int)

    def poll_once(self, timeout: float = 1.0) -> dict | None:
        """Poll for a single message and increment its anomaly_type's
        counter. Returns the parsed event dict, or None if nothing was
        available or the message errored."""
        msg = self._consumer.poll(timeout)
        if msg is None:
            return None
        if msg.error():
            print(f"Consumer error: {msg.error()}")
            return None

        event = json.loads(msg.value())
        self.counts_by_anomaly_type[event["anomaly_type"]] += 1
        return event

    def close(self) -> None:
        self._consumer.close()
