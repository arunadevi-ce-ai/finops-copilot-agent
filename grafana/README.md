# FinOps Copilot — Grafana dashboard

`finops-dashboard.json` reads the four metrics defined in `metrics.py`.
No pipeline-health panel — this shows the copilot's decisions
(findings, proposals, confirmation status), not its own uptime.

This is the local, no-Kubernetes way to check the dashboard. For the
real deployment, see `infra/monitoring/README.md` instead.

| Panel | Type | Query |
|---|---|---|
| Total Projected Savings | Stat | `sum(finops_projected_savings_dollars_total)` |
| Anomalies Detected Over Time | Time series, by `anomaly_type` | `sum by (anomaly_type) (finops_anomalies_detected_total)` |
| Recommendations by Status | Bar chart | `sum by (status) (finops_recommendations_total)` |
| Open Recommendations | Table, by `team` + `anomaly_type` | `finops_open_recommendations > 0` |

## 1. Expose the metrics endpoint

Run anything that calls `metrics.start_metrics_server()` — the simplest
is `orchestrator.py`'s own demo entry point, from `agent_code/`:

```
python orchestrator.py
```

This serves `/metrics` on `http://localhost:8000/metrics`. Leave it
running (or run the test suite, e.g. `pytest test_metrics.py`, to
populate a few data points first — the process must stay alive for
Prometheus to keep scraping it).

## 2. Point a local Prometheus at it

Install Prometheus locally (e.g. `brew install prometheus`, or download
from prometheus.io), and add a scrape config:

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: finops-copilot
    static_configs:
      - targets: ["localhost:8000"]
```

Start it pointed at that config:

```
prometheus --config.file=prometheus.yml
```

Confirm it's scraping: open `http://localhost:9090/targets` and check
the `finops-copilot` job is `UP`.

## 3. Run Grafana and import the dashboard

Install/run Grafana locally (e.g. `brew install grafana && brew services start grafana`,
or the official Docker image), then:

1. Open `http://localhost:3000` (default login `admin` / `admin`).
2. Add a **Prometheus** data source pointing at `http://localhost:9090`
   (Connections → Data sources → Add data source).
3. Go to **Dashboards → New → Import**, upload `finops-dashboard.json`
   (or paste its contents).
4. When prompted, select the Prometheus data source you just added for
   the `DS_PROMETHEUS` variable, then **Import**.

## 4. Generate some data to look at

The dashboard will be empty until something increments the metrics.
From `agent_code/`, with the metrics server running (step 1):

```
python -m pytest test_metrics.py -s   # DR-volume trap case, real OpenAI + Chroma calls
python -m pytest test_orchestrator.py -s   # idle-GPU case too, plus a resumed approval
```

Each run adds real counter/gauge values — refresh the dashboard (top
right, or set the 30s auto-refresh already configured) to see them land.
