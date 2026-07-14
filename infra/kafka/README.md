# Kafka (Bitnami chart)

Single-node Kafka (KRaft mode, no ZooKeeper) via the `bitnami/kafka`
Helm chart. Verified live on GKE Autopilot — producer and consumer
both confirmed working end to end.

Assumes the `finops-copilot` namespace already exists (created by
Terraform in `infra-copilot-agent`).

## Install

```bash
bash setup-kafka.sh
```

Idempotent, safe to re-run. Installs the chart, waits for the broker,
creates the `billing.anomaly.detected` topic.

Verify:

```bash
bash verify-kafka.sh > kafka-verify-output.txt 2>&1
```

Tear down:

```bash
helm uninstall finops-kafka -n finops-copilot
kubectl delete pvc data-finops-kafka-controller-0 -n finops-copilot
```

## Bootstrap address

```
finops-kafka.finops-copilot.svc.cluster.local:9092
```

In-cluster only. Port-forwarding this and connecting from outside
doesn't work — Kafka redirects clients to each broker's internal DNS
name after the first connection. Run producer/consumer test code via
`kubectl exec` into a pod that already has the app image instead.

## Notes

- Not using Strimzi — its operator crash-looped on this cluster for
  reasons never fully diagnosed. Bitnami's chart runs Kafka directly
  as a StatefulSet, no operator to fail.
- The chart's default image tag was pulled from Docker Hub's free tier
  (Broadcom/Bitnami licensing change). `kafka-values.yaml` points at
  `bitnamilegacy/kafka` instead — check
  [hub.docker.com/r/bitnamilegacy/kafka](https://hub.docker.com/r/bitnamilegacy/kafka/tags)
  if that ever 404s too.
- `auto.create.topics.enable` defaults to `false` on this chart, which
  silently breaks every consumer group (Kafka's internal
  `__consumer_offsets` topic never gets created). Fixed via
  `KAFKA_CFG_AUTO_CREATE_TOPICS_ENABLE=true` plus replication-factor-1
  overrides for the internal topics (only one broker here).
