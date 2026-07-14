#!/bin/bash
# Verifies monitoring is genuinely working end-to-end against the real
# GKE deployment: pod status, Grafana dashboard load, and confirmation
# that Prometheus is actually scraping finops-copilot-api's /metrics.
# Touches the live cluster — this isn't invoked automatically by anything.
#
# Run it with output captured externally, not via internal tee (avoids
# shell-specific tee quirks):
#   bash infra/monitoring/verify-monitoring.sh > monitoring-verify-output.txt 2>&1

set -uo pipefail

echo "=== Pod status (all pods in namespace — avoids guessing this chart's exact label selector) ==="
kubectl get pods -n finops-copilot

echo ""
echo "=== Grafana Service name (confirm it matches README.md) ==="
kubectl get svc -n finops-copilot | grep -i grafana

echo ""
echo "=== Prometheus Service name (confirm it matches README.md) ==="
kubectl get svc -n finops-copilot | grep -i prometheus

echo ""
echo "=== dashboard-configmap.yaml applied and labeled correctly ==="
kubectl get configmap -n finops-copilot finops-copilot-dashboard -o jsonpath='{.metadata.labels}'
echo ""

echo ""
echo "=== finops-copilot-api Service's own labels (what the ServiceMonitor's selector actually matches against) ==="
kubectl get svc -n finops-copilot finops-copilot-api -o jsonpath='{.metadata.labels}'
echo ""

echo ""
echo "=== ServiceMonitor present ==="
kubectl get servicemonitor -n finops-copilot finops-copilot-api

echo ""
echo "=== Does the finops-copilot-api Service actually have a ready backend pod? ==="
kubectl get endpoints -n finops-copilot finops-copilot-api

echo ""
echo "=== Is Prometheus actually scraping finops-copilot-api? (precise query, not a full dump) ==="
kubectl port-forward -n finops-copilot svc/kube-prometheus-stack-prometheus 9090:9090 > /dev/null 2>&1 &
PF_PID=$!
sleep 3
TARGETS_FILE="prom-targets.json"
curl -s "http://localhost:9090/api/v1/targets" > "$TARGETS_FILE"
python3 -c "
import json
with open('$TARGETS_FILE') as f:
    data = json.load(f)

def show(section):
    targets = data.get('data', {}).get(section, [])
    matches = [t for t in targets if 'finops-copilot-api' in t.get('discoveredLabels', {}).get('job', '')]
    print(f'{section}: {len(matches)} matching target(s)')
    for t in matches:
        labels = t.get('labels', t.get('discoveredLabels', {}))
        print(f\"  address={labels.get('instance', labels.get('__address__'))} health={t.get('health')} lastError={t.get('lastError')!r}\")

show('activeTargets')
show('droppedTargets')
"

echo ""
echo "=== Does Prometheus actually have FinOps metric data for panels to show? ==="
curl -s 'http://localhost:9090/api/v1/query?query=finops_anomalies_detected_total' \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
results = data.get('data', {}).get('result', [])
print(f'{len(results)} time series for finops_anomalies_detected_total')
for r in results[:5]:
    print(f\"  {r['metric']} = {r['value'][1]}\")
"
kill $PF_PID 2>/dev/null

echo ""
echo "=== Grafana: was the dashboard actually loaded by the sidecar? ==="
kubectl port-forward -n finops-copilot svc/kube-prometheus-stack-grafana 3000:80 > /dev/null 2>&1 &
PF_PID=$!
sleep 3
GRAFANA_PASSWORD=$(kubectl get secret kube-prometheus-stack-grafana -n finops-copilot -o jsonpath='{.data.admin-password}' | base64 -d)
GRAFANA_AUTH="admin:${GRAFANA_PASSWORD}"
echo "--- Dashboard search ---"
curl -s -u "$GRAFANA_AUTH" 'http://localhost:3000/api/search?query=FinOps' | python3 -c "
import json, sys
dashboards = json.load(sys.stdin)
print(f'{len(dashboards)} dashboard(s) found matching \"FinOps\"')
for d in dashboards:
    print(f\"  title={d.get('title')!r} uid={d.get('uid')!r}\")
"
echo ""
echo "--- Datasources (confirms the \${DS_PROMETHEUS} template variable has something to resolve to) ---"
curl -s -u "$GRAFANA_AUTH" 'http://localhost:3000/api/datasources' | python3 -c "
import json, sys
sources = json.load(sys.stdin)
print(f'{len(sources)} datasource(s)')
for s in sources:
    print(f\"  name={s.get('name')!r} type={s.get('type')!r} isDefault={s.get('isDefault')}\")
"
kill $PF_PID 2>/dev/null

echo ""
echo "Done."
