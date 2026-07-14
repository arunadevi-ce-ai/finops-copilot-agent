"""
Consumer for the orchestrator side of the billing.anomaly.detected
fan-out. Reconstructs a detector.Finding directly from the event's own
fields (resource_name, anomaly_type, detail.reason already carry
everything needed — no reason to re-run detector.run_detector() and
risk it returning something different by the time the event is
processed), runs it through the real orchestrator.py pipeline up to
human_approval's interrupt(), and stores an approval token the same
way api.py's /propose does — so a proposal triggered automatically by
a Kafka event can be approved through the exact same POST /confirm as
one triggered by a direct API call.

Kafka only guarantees at-least-once delivery: a crash between running
the pipeline and the offset committing (or a consumer-group
rebalance) redelivers the same message. Without a guard against that,
redelivery means a second full (paid) LLM run and a second approval
token for a proposal a human may have already acted on. poll_once()
closes this two ways together:
  - enable.auto.commit is off — offsets are committed by hand, only
    after a message is fully processed, instead of on librdkafka's own
    timer (which could otherwise commit before processing finishes).
  - a Redis marker, keyed on the event's own identity, is checked
    before doing any work and only set after the pipeline succeeds —
    so a genuine duplicate delivery is skipped, but a crash mid-
    pipeline still leaves the message uncommitted and gets retried.
"""

import json
import uuid

from confluent_kafka import Consumer

import approvals
import detector
import orchestrator
from events.producer import TOPIC

# Covers any realistic crash/restart or consumer-group rebalance
# window — this is an idempotency guard, not a business-rule dedup
# window, so it only needs to outlive how long a redelivery could
# plausibly lag the original delivery.
DEDUP_TTL_SECONDS = 86400


class OrchestratorConsumer:
    def __init__(self, bootstrap_servers: str, group_id: str = "finops-orchestrator"):
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([TOPIC])

    @staticmethod
    def _dedup_key(event: dict) -> str:
        # resource_name + anomaly_type + detected_at_epoch identifies
        # this specific published event, not just "this resource today"
        # — two genuinely different anomaly events for the same
        # resource/category shouldn't get deduped against each other.
        return (
            f"finops:kafka-processed:{event['resource_name']}:"
            f"{event['anomaly_type']}:{event['detected_at_epoch']}"
        )

    @staticmethod
    def _already_processed(key: str) -> bool:
        return approvals.redis_client().exists(key) == 1

    @staticmethod
    def _mark_processed(key: str) -> None:
        approvals.redis_client().setex(key, DEDUP_TTL_SECONDS, "1")

    def poll_once(self, timeout: float = 1.0) -> dict | None:
        """Poll for a single message and, if one arrives and hasn't
        already been processed, run it through the real orchestrator
        pipeline and store an approval token. Returns a dict with the
        recommendation and approval_token, or None if nothing was
        available, the message errored, or it was a duplicate
        delivery."""
        msg = self._consumer.poll(timeout)
        if msg is None:
            return None
        if msg.error():
            print(f"Consumer error: {msg.error()}")
            return None

        event = json.loads(msg.value())
        dedup_key = self._dedup_key(event)

        if self._already_processed(dedup_key):
            print(f"duplicate delivery, skipping: {event['resource_name']} "
                  f"({event['anomaly_type']} @ {event['detected_at_epoch']})")
            self._consumer.commit(msg)
            return None

        finding = detector.Finding(
            resource_name=event["resource_name"],
            category=event["anomaly_type"],
            detail=event.get("detail", {}).get("reason", ""),
        )

        thread_id = f"kafka-{finding.resource_name}-{uuid.uuid4().hex[:8]}"
        _, result, _ = orchestrator.run_for_finding(finding, thread_id=thread_id)
        token = approvals.create_token(finding.resource_name, thread_id, result["recommendation"])

        # Only reachable once the pipeline and token creation both
        # actually succeeded — if either raised, execution never gets
        # here, so the message stays uncommitted and gets redelivered.
        self._mark_processed(dedup_key)
        self._consumer.commit(msg)

        print(f"proposal ready: {finding.resource_name} -> "
              f"{result['recommendation']['proposed_action']} "
              f"(approval_token={token})")

        return {
            "resource_name": finding.resource_name,
            "recommendation": result["recommendation"],
            "approval_token": token,
        }

    def close(self) -> None:
        self._consumer.close()
