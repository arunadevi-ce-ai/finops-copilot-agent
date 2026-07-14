#!/bin/bash
# Brings up Redis from nothing. Idempotent — safe to re-run against an
# existing install. Touches the live cluster; not invoked automatically
# by anything. Assumes the finops-copilot namespace already exists
# (created by Terraform in the separate infra-copilot-agent repo).
#
# Usage: bash infra/redis/setup-redis.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== Applying Redis Deployment + Service ==="
kubectl apply -f redis-deployment.yaml -f redis-service.yaml

echo ""
echo "=== Waiting for it to be ready ==="
kubectl wait pod -n finops-copilot -l app=finops-redis --for=condition=Ready --timeout=120s

echo ""
echo "=== Done. In-cluster address: ==="
echo "finops-redis.finops-copilot.svc.cluster.local:6379"
