import hashlib
import json
from dataclasses import dataclass


@dataclass
class UsageMeter:
    model_calls: int = 0
    missing_usage: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def __call__(self, response):
        self.model_calls += 1
        usage = getattr(response, "usage", None)
        incoming = getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", None))
        outgoing = getattr(usage, "output_tokens", getattr(usage, "completion_tokens", None))
        if incoming is None or outgoing is None:
            self.missing_usage += 1
            return
        details = getattr(usage, "input_tokens_details", getattr(usage, "prompt_tokens_details", None))
        self.input_tokens += incoming
        self.output_tokens += outgoing
        self.cached_input_tokens += getattr(details, "cached_tokens", 0) or 0

    def report(self, rates=None):
        known = self.missing_usage == 0
        cost = None
        if known and rates is not None:
            cost = ((self.input_tokens - self.cached_input_tokens) * rates[0]
                    + self.cached_input_tokens * rates[1] + self.output_tokens * rates[2]) / 1_000_000
        return {"model_calls": self.model_calls, "missing_usage_calls": self.missing_usage,
                "input_tokens": self.input_tokens if known else None,
                "cached_input_tokens": self.cached_input_tokens if known else None,
                "output_tokens": self.output_tokens if known else None, "estimated_cost_usd": cost}


def findings_hash(findings):
    return hashlib.sha256(json.dumps(findings, sort_keys=True).encode()).hexdigest()


def summarize(cases, runs, adjudications=None):
    """Human semantic labels are required; line overlap is only a candidate hint."""
    by_id = {case.id: case for case in cases}
    labels = adjudications or {}
    summaries = {}
    for mode in sorted({run["mode"] for run in runs}):
        selected = [run for run in runs if run["mode"] == mode]
        completed = [run for run in selected if run["status"] == "completed"]
        clean = [run for run in completed if not by_id[run["case_id"]].expected_bugs]
        buggy = [run for run in completed if by_id[run["case_id"]].expected_bugs]
        detected, false_findings, unknown = 0, 0, 0
        for run in completed:
            case = by_id[run["case_id"]]
            findings = run["findings"]
            if not case.expected_bugs:
                false_findings += len(findings)
                continue
            key = f"{case.id}/{mode}"
            label = labels.get(key)
            if label is None:
                unknown += len(findings)
                continue
            if label["findings_sha256"] != findings_hash(findings):
                raise ValueError(f"Stale adjudication: {key}")
            matches = label["matches"]
            if len(matches) != len(findings):
                raise ValueError(f"Adjudicate every finding: {key}")
            allowed = {bug.id for bug in case.expected_bugs}
            if any(match is not None and match not in allowed for match in matches):
                raise ValueError(f"Unknown expected bug: {key}")
            found = {match for match in matches if match is not None}
            detected += len(found)
            # Duplicate reports cannot inflate recall and count as extra findings.
            false_findings += len(matches) - len(found)
        total_bugs = sum(len(by_id[run["case_id"]].expected_bugs) for run in buggy)
        flagged_clean = sum(bool(run["findings"]) for run in clean)
        costs = [run["usage"]["estimated_cost_usd"] for run in selected]
        token_values = [run["usage"]["input_tokens"] + run["usage"]["output_tokens"]
                        if run["usage"]["input_tokens"] is not None else None for run in selected]
        summaries[mode] = {
            "attempted": len(selected), "completed": len(completed),
            "failed": len(selected) - len(completed), "buggy_completed": len(buggy),
            "clean_completed": len(clean), "expected_bugs_completed": total_bugs,
            "unadjudicated_findings": unknown,
            "bug_recall": detected / total_bugs if total_bugs and not unknown else None,
            "false_positive_findings": false_findings if not unknown else None,
            "false_positive_rate": flagged_clean / len(clean) if clean else None,
            "clean_pr_accuracy": 1 - flagged_clean / len(clean) if clean else None,
            "verified_findings": sum(run["verified_findings"] for run in completed),
            "mean_tool_calls": (sum(run["tool_calls"] for run in selected) / len(selected)
                                if all(run["tool_calls"] is not None for run in selected) else None),
            "mean_latency_seconds": sum(run["latency_seconds"] for run in selected) / len(selected),
            "total_tokens": sum(token_values) if all(value is not None for value in token_values) else None,
            "estimated_cost_usd": sum(costs) if all(cost is not None for cost in costs) else None,
        }
    return summaries
