# Monitoring (kube-prometheus-stack)

Prometheus + Grafana via Helm, plus a ConfigMap for the FinOps
dashboard and a ServiceMonitor for `finops-copilot-api`. Verified live
on GKE Autopilot — Prometheus confirmed scraping `/metrics` with
`health=up`.

Assumes the `finops-copilot` namespace already exists (created by
Terraform in `infra-copilot-agent`).

## Install

```bash
bash setup-monitoring.sh
```

Idempotent, safe to re-run. Installs the chart, loads the dashboard,
applies the ServiceMonitor, waits for Prometheus.

Verify:

```bash
bash verify-monitoring.sh > monitoring-verify-output.txt 2>&1
```

Tear down:

```bash
kubectl delete -f dashboard-configmap.yaml -f service-monitor.yaml
helm uninstall kube-prometheus-stack -n finops-copilot
```

## Reach Grafana

```bash
kubectl port-forward -n finops-copilot svc/kube-prometheus-stack-grafana 3000:80
```

`http://localhost:3000`, user `admin`, password:

```bash
kubectl get secret kube-prometheus-stack-grafana -n finops-copilot -o jsonpath='{.data.admin-password}' | base64 -d
```

(The chart auto-generates this and stores it in a Secret instead of a
plaintext value file, so there's nothing to read out of
`kube-prometheus-stack-values.yaml`.)

## Regenerating the dashboard ConfigMap

If `grafana/finops-dashboard.json` changes, regenerate
`dashboard-configmap.yaml` (see the comment at the top of that file).
Don't hand-edit the embedded JSON.

## Notes

- This chart leaves Prometheus/Grafana/the operator's resources unset
  by default — required on GKE Autopilot. `kube-prometheus-stack-values.yaml`
  sets them explicitly.
- The `ServiceMonitor`'s `selector.matchLabels` matches a Service's own
  `metadata.labels` — a different field from `spec.selector` (which
  only controls pod routing). `infra/api/api-deployment.yaml`'s
  Service was missing `metadata.labels` and Prometheus silently
  dropped the target as a result; fixed in `api-service.yaml`.
- Its `release: kube-prometheus-stack` label is what makes this
  chart's default selector pick the ServiceMonitor up at all.
