# FinOps Copilot

An agent that catches wasted cloud spend, checks it against written company policy before suggesting anything, and never acts without a human saying yes first.

## Why I built this

Cost anomaly detection by itself is easy: flag anything idle, flag anything unattached, done. The hard part is that "looks wasteful" and "is actually wasteful" aren't the same thing. A disk with nothing attached to it might be garbage, or it might be a disaster-recovery backup someone deliberately provisioned that way. A dumb detector can't tell the difference, and a naive AI agent that just eyeballs the numbers will confidently make the same mistake.

So this project is built around that specific failure mode. A cheap, deterministic rule-based layer finds anomalies. A separate reasoning layer checks each one against real policy documents before deciding what to recommend, and it has to cite the exact policy section it's relying on. Nothing gets deleted, resized, or touched without a human approving it, and that approval has to survive a crash or getting picked up by a completely different process. It runs on a real GKE cluster, with a real event pipeline behind it, not just on my laptop.

## Architecture

![FinOps Copilot system flow](Assets/finops_system_flowchart.svg)

| Component | File(s) | Status |
|---|---|---|
| Anomaly detector | `agent_code/detector.py`, rule-based, 5 categories | Verified live |
| Policy retrieval (RAG) | `agent_code/rag.py`, Chroma, structure-aware chunking | Verified live |
| Multi-agent orchestrator | `agent_code/orchestrator.py`, LangGraph: investigate, retrieve, recommend, approval | Verified live |
| REST API | `agent_code/api.py`: `/anomalies`, `/propose`, `/confirm` | Verified live |
| MCP tool server | `agent_code/mcp_server.py`, 3 tools, stdio | Built, tested |
| Event pipeline | `agent_code/events/`, Kafka producer + 2 consumers | Verified live |
| BigQuery backend | Optional billing/utilization/metadata source | Verified live |
| Metrics + dashboard | `agent_code/metrics.py` + Grafana | Verified live |
| Eval harness | `agent_code/evals/`, scenario-based regression checks | Built, tested |
| Infra | `infra/`: Kafka, Redis, monitoring, API, all on K8s | Verified live |
| CI/CD | `.github/workflows/`: build/push, manifest sync, PR validation | Written, not run live |

## How it works

There are four ways to trigger a run, and they all end up in the same place:

1. **Direct Python** — `python detector.py` or `python orchestrator.py`.
2. **REST API** — `POST /propose` (`agent_code/api.py`).
3. **MCP client** (Claude Desktop, etc.) — the `propose_rightsizing` tool.
4. **Kafka event** — a utilization breach gets published to `billing.anomaly.detected`, and `consumer_orchestrator.py` picks it up.

What actually happens once a run starts:

`detector.py` reads billing data (CSV/JSON, or BigQuery) and flags anomalies across 5 categories. It has zero policy awareness. Left alone it will happily flag a disaster-recovery volume for deletion, which is the whole point — something downstream has to catch that.

That something is `orchestrator.py`, a 4-node LangGraph pipeline:

- `investigation_agent` decides whether the evidence actually justifies action and drafts a policy search query.
- `retrieval_agent` searches Chroma and pulls the *whole* matching policy doc, not just the closest chunk, so a short Precedent section can't get lost.
- `recommendation_agent` checks the resource's metadata against the policy text. If it matches a documented exception, it downgrades to "flag for manual review" instead of proposing something destructive.
- `human_approval` calls `interrupt()` and stops. Nothing is final until a person resumes it, either through `Command(resume=...)` or `POST /confirm`. State and one-time approval tokens live in Redis, so whoever resumes it doesn't have to be the same process that paused.

Every finding and every recommendation also emits a Prometheus metric, which is what shows up on the Grafana dashboard.

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Agent orchestration | LangGraph |
| LLM | OpenAI, swappable via `orchestrator.py`'s `_make_llm()` |
| Vector database | Chroma, embedded in-process |
| Event streaming | Kafka (Bitnami Helm chart, KRaft mode) |
| State / tokens | Redis (`redis-stack-server`, needed for RediSearch) |
| Billing data | CSV/JSON, or BigQuery |
| Metrics | Prometheus + Grafana |
| Protocol server | MCP, low-level `Server` API |
| Infrastructure | Helm charts + raw Kubernetes manifests |
| Testing | `pytest` |

## Project structure

```
finops-copilot/
├── agent_code/                  # Docker build context
│   ├── generate_mock_data.py    # Generates data/billing/*
│   ├── detector.py              # Rule-based anomaly detector
│   ├── rag.py                   # Chroma ingestion + retrieve()
│   ├── orchestrator.py          # LangGraph multi-agent graph
│   ├── api.py                   # FastAPI wrapper
│   ├── mcp_server.py            # MCP tool server (stdio)
│   ├── metrics.py               # Prometheus metric definitions
│   ├── approvals.py             # Shared Redis approval-token store
│   ├── data/
│   │   ├── billing/             # Generated mock billing/utilization/metadata
│   │   └── policies/            # 4 FinOps policy docs (RAG source)
│   ├── events/                  # Kafka producer + 2 consumers
│   ├── evals/                   # Scenario-based eval harness
│   ├── test_*.py                # pytest suite
│   ├── requirements.txt
│   └── Dockerfile
├── infra/
│   ├── kafka/                   # Bitnami Kafka: Helm values + setup/verify scripts
│   ├── monitoring/               # kube-prometheus-stack + dashboard + ServiceMonitor
│   ├── redis/                    # Redis Deployment/Service
│   ├── api/                     # API Deployment/Service
│   └── terraform/                # reference copy — applied from a separate, shared repo
├── grafana/                     # dashboard JSON + local import instructions
├── Assets/                      # architecture diagrams
└── .github/workflows/            # build-and-push, sync-manifest-to-cluster, pr-validation
```

## Setup

**Prerequisites:** Python 3.11+, and an OpenAI API key if you want `orchestrator.py` or `mcp_server.py` — `detector.py` and `rag.py` alone don't need one.

```bash
cd agent_code
pip install -r requirements.txt
echo 'OPENAI_API_KEY="sk-..."' > .env
python generate_mock_data.py
python rag.py             # ingests policy docs into Chroma
python -m pytest -q       # confirm everything works
```

## Using it

All commands below run from `agent_code/`.

**Detector alone** (no LLM, no API key needed):
```bash
python detector.py
```

**Policy search alone** (also no LLM):
```bash
python -c "import rag; [print(r['source_file'], '::', r['section_title']) for r in rag.retrieve('orphaned disk that is actually for disaster recovery', top_k=3)]"
```

**Full orchestrator** (needs `OPENAI_API_KEY`):
```bash
python orchestrator.py
```
This runs the DR-volume trap case end to end and pauses at `interrupt()`. To resume it yourself:
```python
from langgraph.types import Command
import detector, orchestrator

finding = next(f for f in detector.run_detector() if f.resource_name == "pd-payments-dr-volume-01")
app, result, config = orchestrator.run_for_finding(finding, thread_id="my-review")
print(result["recommendation"])
final_state = app.invoke(Command(resume={"approved": True, "approver": "you"}), config=config)
```

**REST API** — see `infra/api/README.md` for the deployed version, or run `api.py` locally with `REDIS_URL` set.

**MCP server:**
```bash
python mcp_server.py
```
It talks over stdio, so you need to launch it from an MCP client, not run it standalone. Claude Desktop / Claude Code config:
```json
{
  "mcpServers": {
    "finops-copilot": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/finops-copilot/agent_code",
      "env": { "OPENAI_API_KEY": "sk-..." }
    }
  }
}
```
Three tools are exposed: `get_cost_anomalies` (calls `detector.run_detector()`), `search_finops_policy` (calls `rag.retrieve()`), and `propose_rightsizing` (calls `orchestrator.run_for_finding()`, up to `interrupt()`). Only the last one touches an LLM.

**Metrics + dashboard:**
```bash
python -c "import metrics; metrics.start_metrics_server(port=8000)"
```
From there, follow `grafana/README.md` for a local Prometheus + Grafana setup, or `infra/monitoring/README.md` for the real deployment.

## Testing

```bash
python -m pytest -q                            # everything
python -m pytest -q -s test_orchestrator.py     # see the actual LLM output
```

`test_orchestrator.py`, `test_metrics.py`, and `test_mcp_server.py` make real OpenAI calls on purpose. Mocking the LLM would just be testing that a mock returns what you told it to return. `test_events.py`, `test_watcher.py`, and `test_consumers.py` mock the `confluent_kafka` client instead, since there's no live broker in CI.

## Mock data and the policy trap

`generate_mock_data.py` produces three files under `agent_code/data/billing/`: `billing_export.csv` (9 resources over 14 days), `utilization_metrics.csv` (daily CPU/mem), and `resource_metadata.json` (attachment and last-access facts billing data doesn't carry). They're kept as separate files on purpose, because cost alone can't tell you a resource is idle.

One resource, `pd-payments-dr-volume-01`, is the trap I mentioned above. A naive detector flags it for deletion, but it's tagged `purpose=disaster-recovery`, and that matches a documented precedent in `agent_code/data/policies/policy-fin-07-orphaned-resources.md`. Whether the recommendation step actually catches this is the thing this whole project is trying to prove.

## BigQuery backend (optional)

Billing, utilization, and metadata can come from BigQuery instead of CSV/JSON. Set `BIGQUERY_DATASET` (`project.dataset`) to switch it on; leave it unset and everything reads from CSV/JSON like normal. To create the dataset and tables once:

```bash
bq mk --dataset --location=REGION PROJECT_ID:finops_copilot

bq mk --table PROJECT_ID:finops_copilot.billing_export \
  date:DATE,project_id:STRING,team:STRING,service:STRING,sku_description:STRING,resource_name:STRING,region:STRING,cost_usd:FLOAT64

bq mk --table PROJECT_ID:finops_copilot.utilization_metrics \
  date:DATE,resource_name:STRING,avg_cpu_pct:FLOAT64,avg_mem_pct:FLOAT64

bq mk --table PROJECT_ID:finops_copilot.resource_metadata \
  resource_name:STRING,team:STRING,attached_instance:BOOLEAN,last_accessed_days_ago:INTEGER,extra_attributes:STRING
```

`extra_attributes` is a JSON string holding whatever fields don't fit a fixed schema, things like `machine_type`, `disk_type`, `purpose`, since they vary by resource type. The service account needs `roles/bigquery.dataEditor` on the dataset and `roles/bigquery.jobUser` at the project level.

## Design notes

A few decisions worth knowing the reasoning behind, in case they look arbitrary:

- Chroma runs embedded in-process. No separate vector DB service to stand up.
- Retrieval is two-stage. `retrieve()` finds the right document, then `get_document_chunks()` grabs the whole thing directly by filename, without running another similarity search. That second step deliberately skips vector matching, because vector matching was the reason a policy's Precedent section could get missed in the first place.
- The Kafka trigger is a utilization breach, not a billing event. Billing export is daily-batch and far too slow to react to anything in real time.
- The checkpointer in `orchestrator.py` is `InMemorySaver` by default, and switches to a Redis-backed `RedisSaver` when `REDIS_URL` is set. That's the whole reason `api.py` can resume a paused run from a completely different request or pod.
- Metrics use `status ∈ {proposed, approved, rejected}`. There's no separate "manual review" status. A downgrade to manual review still counts as `rejected`, because the pipeline is refusing to let it through on its own either way.

## Status

Everything above is verified live on a real GKE cluster: Redis, the API, BigQuery (through Workload Identity), and Kafka, including `consumer_orchestrator.py` actually invoking the real orchestrator pipeline off a live Kafka event. Each `infra/*/README.md` has the specific bugs that came up getting it there and how they got fixed.

The one thing not yet exercised is the CI/CD workflows against a real GitHub Actions run. Deploys so far have all been manual, run locally component by component, on purpose, while I'm still iterating on everything else.
