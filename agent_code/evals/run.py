"""
CLI entry point for the eval harness.

    python -m evals.run                        # full run, all 9 scenarios
    python -m evals.run --fast                  # detector-stage only, zero API calls
    python -m evals.run --scenario dr_volume_trap
    python -m evals.run --repeat 10              # reliability audit (LLM-backed scenarios only)
    python -m evals.run --no-write               # skip JSON/CSV artifacts
"""

import argparse
import dataclasses
import sys

from evals.report import append_csv_history, format_human_report, write_json_result
from evals.runner import run_all
from evals.scenarios import SCENARIOS


def main() -> int:
    parser = argparse.ArgumentParser(description="FinOps Copilot eval harness")
    parser.add_argument("--scenario", help="Run a single scenario by id")
    parser.add_argument("--fast", action="store_true", help="Detector-stage only, zero API calls")
    parser.add_argument("--no-write", action="store_true", help="Skip writing JSON/CSV artifacts")
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Repeat LLM-backed scenarios N times and report a pass rate (reliability audit)",
    )
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s.id == args.scenario]
        if not scenarios:
            known = [s.id for s in SCENARIOS]
            print(f"Unknown scenario id: {args.scenario!r}. Known ids: {known}")
            return 2

    if args.fast:
        scenarios = [dataclasses.replace(s, run_orchestrator=False) for s in scenarios]

    report = run_all(scenarios, repeat=args.repeat)
    print(format_human_report(report))

    if not args.no_write:
        json_path = write_json_result(report)
        append_csv_history(report)
        print(f"\nResults written to {json_path}")
        print("History appended to evals/results/history.csv")

    has_errors = any(r.error for r in report.results)
    return 0 if report.n_hard_fail == 0 and not has_errors else 1


if __name__ == "__main__":
    sys.exit(main())
