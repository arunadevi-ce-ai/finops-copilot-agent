"""
Publishes billing-anomaly events to the finops-kafka cluster deployed
by infra/kafka/. Uses confluent-kafka, the real client library, not a
stand-in.

No live broker to test against here, so test_events.py mocks
confluent_kafka.Producer directly — this checks that the message gets
built and serialized correctly, not that a real broker accepts it.
"""

import json
import time
from dataclasses import asdict, dataclass
from typing import Literal

from confluent_kafka import Producer

TOPIC = "billing.anomaly.detected"

AnomalyType = Literal[
    "oversized_or_idle_compute",
    "orphaned_disk",
    "orphaned_static_ip",
    "cost_spike",
    "storage_lifecycle_candidate",
]


@dataclass
class BillingAnomalyEvent:
    resource_name: str
    team: str
    project_id: str
    anomaly_type: AnomalyType
    detected_at_epoch: int
    daily_cost_usd: float
    detail: dict  # anomaly-specific evidence, e.g. {"avg_cpu_pct": 4.2}

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class AnomalyEventProducer:
    def __init__(self, bootstrap_servers: str):
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish(self, event: BillingAnomalyEvent) -> None:
        self._producer.produce(
            topic=TOPIC,
            key=event.resource_name,
            value=event.to_json(),
            callback=self._delivery_callback,
        )
        self._producer.poll(0)  # trigger any pending delivery callbacks

    def flush(self, timeout: float = 10.0) -> int:
        """Block until all outstanding messages are delivered or the
        timeout elapses. Returns the number of messages still undelivered
        (0 means everything was acked)."""
        return self._producer.flush(timeout)

    @staticmethod
    def _delivery_callback(err, msg):
        if err is not None:
            print(f"Delivery failed for {msg.key()}: {err}")
        else:
            print(f"Delivered {msg.key()} to {msg.topic()} [{msg.partition()}]")


def make_event(resource_name: str, team: str, project_id: str,
                anomaly_type: AnomalyType, daily_cost_usd: float, detail: dict) -> BillingAnomalyEvent:
    return BillingAnomalyEvent(
        resource_name=resource_name,
        team=team,
        project_id=project_id,
        anomaly_type=anomaly_type,
        detected_at_epoch=int(time.time()),
        daily_cost_usd=daily_cost_usd,
        detail=detail,
    )
