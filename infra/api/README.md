# FinOps Copilot API

`agent_code/api.py`, containerized, deployed here. REST only —
`GET /health`, `GET /anomalies`, `POST /propose`, `POST /confirm`.
`mcp_server.py` stays stdio-only and separate.

## Create the runtime secret (once, by hand)

```bash
kubectl create secret generic finops-copilot-secrets \
  --from-literal=OPENAI_API_KEY="sk-..." \
  -n finops-copilot
```

Never commit a real key. This is the only place a real
`OPENAI_API_KEY` should exist outside your own `.env`.

## Deploy

Manual today — build the image, push it, then apply the manifests
below. Two GitHub Actions workflows exist for automating this
(`build-and-push.yml`, `sync-manifest-to-cluster.yml`) but haven't
been exercised against a real pipeline run yet.

By hand:

```bash
sed -i 's|image: .*/finops-copilot-agent:.*|image: <your-image-ref>|' api-deployment.yaml
kubectl apply -f api-deployment.yaml
kubectl apply -f api-service.yaml
```

Assumes `infra/redis/` is already applied.

## Reach it

No ingress — internal only, same as Kafka and Grafana.

```bash
kubectl port-forward -n finops-copilot svc/finops-copilot-api 8000:8000
curl localhost:8000/health
curl localhost:8000/anomalies
curl -X POST localhost:8000/propose -H 'content-type: application/json' \
  -d '{"resource_name": "pd-payments-dr-volume-01"}'
# -> returns an approval_token
curl -X POST localhost:8000/confirm -H 'content-type: application/json' \
  -d '{"token": "<approval_token from above>", "approved": true, "approver": "you"}'
```

## Notes

- Resource requests/limits and probe timings are reasonable defaults,
  not load-tested.
