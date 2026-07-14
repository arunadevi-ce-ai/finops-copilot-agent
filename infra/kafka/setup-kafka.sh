#!/bin/bash
# Brings up Kafka (Bitnami chart, KRaft mode) from nothing. Idempotent —
# safe to re-run against an existing install. Touches the live cluster;
# not invoked automatically by anything. Assumes the finops-copilot
# namespace already exists (created by Terraform in the separate
# infra-copilot-agent repo).
#
# Usage: bash infra/kafka/setup-kafka.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== Installing/upgrading Bitnami Kafka ==="
helm repo add bitnami https://charts.bitnami.com/bitnami > /dev/null
helm repo update > /dev/null
helm upgrade --install finops-kafka bitnami/kafka \
  --namespace finops-copilot \
  -f kafka-values.yaml \
  --timeout 10m

echo ""
echo "=== Waiting for the broker to be ready ==="
kubectl wait pod -n finops-copilot -l app.kubernetes.io/instance=finops-kafka \
  --for=condition=Ready --timeout=300s

echo ""
echo "=== Creating billing.anomaly.detected (idempotent — no-op if it already exists) ==="
kubectl exec -n finops-copilot finops-kafka-controller-0 -- \
  kafka-topics.sh --create --topic billing.anomaly.detected \
  --partitions 3 --replication-factor 1 \
  --bootstrap-server localhost:9092 --if-not-exists

echo ""
echo "=== Done. Bootstrap address for in-cluster clients: ==="
echo "finops-kafka.finops-copilot.svc.cluster.local:9092"
echo ""
echo "Run infra/kafka/verify-kafka.sh for a full producer+consumer round-trip check."
