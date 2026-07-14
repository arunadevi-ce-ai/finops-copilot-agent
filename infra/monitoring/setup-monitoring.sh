#!/bin/bash
# Brings up monitoring (kube-prometheus-stack: Prometheus + Grafana)
# from nothing. Idempotent — safe to re-run against an existing
# install. Touches the live cluster; not invoked automatically by
# anything. Assumes the finops-copilot namespace already exists
# (created by Terraform in the separate infra-copilot-agent repo).
#
# The ServiceMonitor here selects infra/api/'s finops-copilot-api
# Service — apply that too for Prometheus to actually have something
# to scrape. This script doesn't fail without it; the target just
# won't appear until it exists.
#
# Usage: bash infra/monitoring/setup-monitoring.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== Installing/upgrading kube-prometheus-stack ==="
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts > /dev/null
helm repo update > /dev/null
# --wait covers the operator and Grafana Deployments (which Helm
# templates directly); it does NOT cover the Prometheus StatefulSet
# itself, since that's created indirectly by the operator reacting to
# the Prometheus custom resource, not something Helm tracks — that's
# handled by the separate named wait below instead of guessing a
# label selector (Grafana/operator and the actual Prometheus pod use
# different app.kubernetes.io/instance values on this chart, confirmed
# live — a shared selector would silently miss one of them).
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace finops-copilot \
  --version 70.2.0 \
  -f kube-prometheus-stack-values.yaml \
  --wait --timeout 10m

echo ""
echo "=== Loading the FinOps Copilot dashboard (Grafana sidecar ConfigMap) ==="
kubectl apply -f dashboard-configmap.yaml

echo ""
echo "=== Applying the ServiceMonitor for finops-copilot-api ==="
kubectl apply -f service-monitor.yaml

echo ""
echo "=== Waiting for the Prometheus StatefulSet specifically ==="
kubectl wait pod/prometheus-kube-prometheus-stack-prometheus-0 -n finops-copilot \
  --for=condition=Ready --timeout=180s || echo "(non-fatal — check 'kubectl get pods -n finops-copilot' if this timed out)"

echo ""
echo "=== Done. Reach them via: ==="
echo "kubectl port-forward -n finops-copilot svc/kube-prometheus-stack-grafana 3000:80    # http://localhost:3000, user admin"
echo "  password: kubectl get secret kube-prometheus-stack-grafana -n finops-copilot -o jsonpath='{.data.admin-password}' | base64 -d"
echo "kubectl port-forward -n finops-copilot svc/kube-prometheus-stack-prometheus 9090:9090"
echo ""
echo "Run infra/monitoring/verify-monitoring.sh for a full scrape/dashboard/data check."
