"""
Report formatting and persistence for eval runs: a human-readable
console report, a full-detail JSON snapshot per run, and an append-only
CSV history row — the actual "regression tracked over time" artifact.
"""

import csv
import json
from dataclasses import asdict
from pathlib import Path

from evals.runner import EvalReport

RESULTS_DIR = Path(__file__).parent / "results"
HISTORY_CSV = RESULTS_DIR / "history.csv"

HISTORY_COLUMNS = [
    "timestamp", "git_sha", "n_scenarios", "n_hard_pass", "n_hard_fail",
    "citation_in_acceptable_set", "retrieval_top1", "retrieval_top3",
]


def format_human_report(report: EvalReport) -> str:
    lines = [
        "FinOps Copilot - Eval Report",
        f"Run: {report.run_timestamp}  |  git: {report.git_sha or 'unknown'}  |  "
        f"{len(report.results)} scenarios",
        "",
        "HARD CHECKS (must always pass)",
    ]

    for r in report.results:
        status = "PASS" if r.overall_pass else "FAIL"
        check_names = ", ".join(c.name for c in r.hard_checks) or "(detector-only)"
        lines.append(f"  [{status}] {r.scenario_id:28s} {check_names}")
        if not r.overall_pass:
            for c in r.hard_checks:
                if not c.passed:
                    lines.append(f"           x {c.name}: {c.detail}")
            if r.error:
                lines.append(f"           x error: {r.error}")

    lines.append("")
    outcome = "ALL HARD CHECKS PASS" if report.n_hard_fail == 0 else f"{report.n_hard_fail} FAILING"
    lines.append(f"  {report.n_hard_pass}/{len(report.results)} scenarios: {outcome}")

    soft = report.soft_metrics
    if soft:
        lines.append("")
        lines.append("SOFT METRICS (tracked, not blocking - expect natural run-to-run variance)")
        for name, rate in soft.items():
            lines.append(f"  {name:32s}: {rate:.0%}")

    if report.reliability:
        lines.append("")
        lines.append("RELIABILITY AUDIT (repeated trials, LLM-backed scenarios only)")
        for rel in report.reliability.values():
            lines.append(f"  {rel.scenario_id:24s} {rel.n_passed}/{rel.n_trials} passed  ({rel.pass_rate:.0%})")

    return "\n".join(lines)


def write_json_result(report: EvalReport, out_dir: Path = RESULTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = report.run_timestamp.replace(":", "").replace("-", "").split(".")[0]
    path = out_dir / f"eval_{ts}.json"
    path.write_text(json.dumps(asdict(report), indent=2, default=str))
    return path


def append_csv_history(report: EvalReport, path: Path = HISTORY_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    soft = report.soft_metrics
    row = {
        "timestamp": report.run_timestamp,
        "git_sha": report.git_sha or "",
        "n_scenarios": len(report.results),
        "n_hard_pass": report.n_hard_pass,
        "n_hard_fail": report.n_hard_fail,
        "citation_in_acceptable_set": soft.get("citation_in_acceptable_set", ""),
        "retrieval_top1": soft.get("retrieval_top1", ""),
        "retrieval_top3": soft.get("retrieval_top3", ""),
    }
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
