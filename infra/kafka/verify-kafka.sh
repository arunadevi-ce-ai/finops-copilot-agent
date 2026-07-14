#!/bin/bash
# Verifies Kafka is genuinely working end-to-end against the real GKE
# deployment: __consumer_offsets health, topic creation, and a real
# publish+consume round-trip. Run from the repo root. Touches the live
# cluster — this isn't invoked automatically by anything.
#
# Output goes to kafka-verify-output.txt (repo root) as well as stdout,
# so you can just share that one file instead of pasting terminal output.

set -uo pipefail

OUT="kafka-verify-output.txt"
: > "$OUT"

log() {
  echo "$1" | tee -a "$OUT"
}

log "=== Kafka controller pod status ==="
kubectl get pods -n finops-copilot -l app.kubernetes.io/instance=finops-kafka 2>&1 | tee -a "$OUT"

log ""
log "=== KAFKA_CFG_* env vars actually on the container ==="
kubectl exec -n finops-copilot finops-kafka-controller-0 -c kafka -- env 2>&1 | grep KAFKA_CFG | tee -a "$OUT"

log ""
log "=== Live broker config: offsets.topic.replication.factor, auto.create.topics.enable ==="
kubectl exec -n finops-copilot finops-kafka-controller-0 -c kafka -- \
  kafka-configs.sh --bootstrap-server localhost:9092 \
  --entity-type brokers --entity-default --describe --all 2>&1 \
  | grep -i "offsets.topic.replication.factor\|auto.create.topics.enable\|transaction.state.log" | tee -a "$OUT"

log ""
log "=== __consumer_offsets topic status (before) ==="
kubectl exec -n finops-copilot finops-kafka-controller-0 -- \
  kafka-topics.sh --describe --topic __consumer_offsets --bootstrap-server localhost:9092 2>&1 | tee -a "$OUT"

log ""
log "=== Manually create __consumer_offsets (in case auto.create.topics.enable is false) ==="
kubectl exec -n finops-copilot finops-kafka-controller-0 -- \
  kafka-topics.sh --create --topic __consumer_offsets \
  --partitions 50 --replication-factor 1 \
  --config cleanup.policy=compact \
  --bootstrap-server localhost:9092 --if-not-exists 2>&1 | tee -a "$OUT"

log ""
log "=== Ensure billing.anomaly.detected topic exists ==="
kubectl exec -n finops-copilot finops-kafka-controller-0 -- \
  kafka-topics.sh --create --topic billing.anomaly.detected \
  --partitions 3 --replication-factor 1 --bootstrap-server localhost:9092 --if-not-exists 2>&1 | tee -a "$OUT"

log ""
log "=== Publish + consume round-trip (runs inside finops-copilot-api pod) ==="
kubectl exec -n finops-copilot deployment/finops-copilot-api -- python -c "
from events.watcher import watch_and_publish
from events.producer import AnomalyEventProducer
from events.consumer_dashboard_counter import DashboardCounterConsumer

producer = AnomalyEventProducer(bootstrap_servers='finops-kafka.finops-copilot.svc.cluster.local:9092')
published = watch_and_publish(producer)
print(f'Published {len(published)} events')

consumer = DashboardCounterConsumer(bootstrap_servers='finops-kafka.finops-copilot.svc.cluster.local:9092', group_id='dashboard-verify-v4')
for i in range(15):
    event = consumer.poll_once(timeout=2.0)
    print(f'poll {i}: {event}')
print(f'Counts by anomaly_type: {dict(consumer.counts_by_anomaly_type)}')
consumer.close()
" 2>&1 | tee -a "$OUT"

log ""
log "=== __consumer_offsets topic status (after) ==="
kubectl exec -n finops-copilot finops-kafka-controller-0 -- \
  kafka-topics.sh --describe --topic __consumer_offsets --bootstrap-server localhost:9092 2>&1 | tee -a "$OUT"

log ""
log "Done — full output saved to $OUT"
