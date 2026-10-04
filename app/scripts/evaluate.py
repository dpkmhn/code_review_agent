"""CLI evaluation. Live OpenAI calls require the explicit --live switch."""

import argparse
import asyncio
import json
import math
import os
from importlib.metadata import version
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation.dataset import load_dataset
from app.evaluation.metrics import summarize

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results/latest.json")
    parser.add_argument("--live", action="store_true", help="Make paid OpenAI requests")
    parser.add_argument("--modes", nargs="+", choices=["diff_only", "context", "verification"],
                        default=["diff_only", "context", "verification"])
    parser.add_argument("--case", action="append", help="Restrict to a case ID (repeatable)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--verification-reasoning", choices=["none", "low", "medium", "high"],
                        help="Override verification_reasoning_effort for the tool-calling reviewer")
    parser.add_argument("--input-rate", type=float, help="USD per million uncached input tokens")
    parser.add_argument("--cached-input-rate", type=float, help="USD per million cached input tokens")
    parser.add_argument("--output-rate", type=float, help="USD per million output tokens")
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--score-report", type=Path, help="Rescore a saved report without API calls")
    args = parser.parse_args()
    cases, digest = load_dataset(args.dataset)
    labels = json.loads(args.adjudications.read_text()) if args.adjudications else None
    if args.score_report:
        report = json.loads(args.score_report.read_text())
        if report["dataset_sha256"] != digest:
            parser.error("Saved report does not match the dataset")
        ids = set(report["case_ids"])
        cases = [case for case in cases if case.id in ids]
        report["summary"] = summarize(cases, report["runs"], labels)
    else:
        if args.case:
            if set(args.case) - {case.id for case in cases}:
                parser.error("Unknown case ID")
            cases = [case for case in cases if case.id in args.case]
        if args.limit is not None:
            if args.limit < 1:
                parser.error("--limit must be positive")
            cases = cases[:args.limit]
        rates = [args.input_rate, args.cached_input_rate, args.output_rate]
        if any(rate is not None for rate in rates):
            if any(rate is None or not math.isfinite(rate) or rate < 0 for rate in rates):
                parser.error("Provide all three nonnegative finite token rates")
        else:
            rates = None
        # The application settings require credentials even for dependency-free fixtures.
        if not args.live:
            os.environ.setdefault("github_token", "offline-unused")
            os.environ.setdefault("openai_api_key", "offline-unused")
        from app.config import settings
        from app.evaluation.runner import run_suite

        if args.verification_reasoning:
            settings.verification_reasoning_effort = args.verification_reasoning

        def progress(run):
            print(f"{run['case_id']} / {run['mode']}: {run['status']}", flush=True)

        runs = asyncio.run(run_suite(cases, list(dict.fromkeys(args.modes)), offline=not args.live,
                                    rates=rates, progress=progress))
        report = {
            "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "run_kind": "live" if args.live else "offline_wiring_check",
            "model": "gpt-6-luna" if args.live else None, "dataset_sha256": digest,
            "verification_reasoning_effort": settings.verification_reasoning_effort,
            "case_ids": [case.id for case in cases], "rates_usd_per_million": rates,
            "repository_search": "literal case-insensitive search on base snapshot",
            "sandbox_execution": False, "runs": runs, "summary": summarize(cases, runs, labels),
            "package_versions": {name: version(name) for name in ("openai", "langgraph", "langchain-core")},
            "limits": {"hypotheses": 5, "context_queries": 3, "context_files": 3,
                       "tool_calls": 30, "verification_rounds": 3, "calls_per_hypothesis": 6},
        }
    if report["run_kind"] == "offline_wiring_check":
        for summary in report["summary"].values():
            for metric in ("bug_recall", "false_positive_findings", "false_positive_rate", "clean_pr_accuracy"):
                summary[metric] = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Saved report: {args.output.resolve()}")
    if any(run["status"] != "completed" for run in report["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
