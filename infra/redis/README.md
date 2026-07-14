# Redis

Backs the LangGraph checkpointer (`orchestrator.py`, when `REDIS_URL`
is set) and the one-time approval token store (`api.py`'s `/propose`
→ `/confirm`).

Runs `redis/redis-stack-server`, not plain Redis — `RedisSaver` needs
the RediSearch module. Plain `redis:7-alpine` fails on the first
`/propose` call with `unknown command 'FT._LIST'`.

## Install

```bash
bash setup-redis.sh
```

or by hand: `kubectl apply -f redis-deployment.yaml -f redis-service.yaml`

Assumes the `finops-copilot` namespace already exists (created by
Terraform in `infra-copilot-agent`).

## In-cluster address

```
finops-redis.finops-copilot.svc.cluster.local:6379
```

`REDIS_URL=redis://finops-redis.finops-copilot.svc.cluster.local:6379/0`

## Notes

- No auth, no persistence — internal-only, data loss on restart is
  accepted at this scale. Add a PVC for persistence, `--requirepass` +
  a matching secret for auth, if ever needed.
- `redis/redis-stack-server:latest` — pin a version for reproducibility
  once confirmed working.

## Tear down

```bash
kubectl delete -f redis-deployment.yaml -f redis-service.yaml
```
