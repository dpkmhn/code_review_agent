# Stage 11 evaluation

The starter dataset contains 20 synthetic buggy PRs and 10 synthetic clean PRs.
These are small, authored Python examples, not historical GitHub PRs or a
representative production benchmark. Each fixture includes complete base/head
snapshots and known bugs with a concrete trigger and consequence. Two cases
require caller or configuration context. Labels stay outside model prompts.
Dataset bytes and snapshots are hashed in reports to pin the evaluation input.

Run the offline wiring check from the project root:

```powershell
uv run python -m app.scripts.evaluate
```

This executes 90 case/mode combinations without OpenAI or GitHub requests. The
stub returns empty predictions, so quality metrics are deliberately null. It
checks graph wiring, report generation, and fixture handling; it does not measure
model quality. Reports default to `evaluation/results/latest.json` and are ignored
by Git. Live runs overwrite that file unless a different `--output` is selected.

For an initial live model run, configure `openai_api_key` in `.env`, then use:

```powershell
uv run python -m app.scripts.evaluate --live --limit 1 --output evaluation/results/live-smoke.json
uv run python -m app.scripts.evaluate --live --output evaluation/results/live.json
```

`--live` makes paid requests and sends fixture code to OpenAI. The CLI runs
sequentially, with no persistence or GitHub API calls. `--case empty-average`
selects a specific case; `--modes diff_only verification` selects architectures.
All modes use the existing GPT-6 Luna services and prompts:

| Mode | Behavior |
| --- | --- |
| `diff_only` | Structured review using only the PR diff |
| `context` | LangGraph context analysis/retrieval, then structured review |
| `verification` | Full multi-agent hypothesis/evidence graph (supervisor and specialists) using local repository tools |

Search uses literal, case-insensitive text on the base snapshot rather than
GitHub's query language. Reads use exact base/head hashes. Verification exposes
only `read_file`, `search_code`, and `find_references`. Sandbox execution is
excluded: this dataset measures inspection-based verification, not execution
verification. Fixture code is never executed by the evaluator.

## Scoring

Review every finding on a buggy PR by meaning, comparing its trigger and
consequence with `expected_bugs`. Location overlap is recorded only as a candidate
hint. It cannot establish a true positive. Create an adjudication JSON file:

```json
{
  "empty-average/verification": {
    "findings_sha256": "copy the hash from this case/mode's report entry",
    "matches": ["empty-average-bug", null]
  }
}
```

`matches` has one entry per finding in report order. Use the matching bug ID or
null for a false positive. Each case/mode is adjudicated separately. Repeated
findings for the same bug count once toward recall and as extra false findings.
The findings hash rejects labels from a different output. A PR with no findings
needs no adjudication. All findings on a clean PR count as false positives under
the dataset's clean label.

Rescore the saved live report without making API calls:

```powershell
uv run python -m app.scripts.evaluate --score-report evaluation/results/live.json --adjudications evaluation/adjudications.json --output evaluation/results/scored.json
```

| Metric | Definition |
| --- | --- |
| Bug recall | Distinct matched bugs / expected bugs in completed buggy PRs; null while any buggy finding is unadjudicated |
| False-positive rate | Completed clean PRs with one or more findings / completed clean PRs |
| False-positive findings | Unmatched and duplicate findings across completed PRs; null while adjudication is incomplete |
| Clean-PR accuracy | Completed clean PRs with no findings / completed clean PRs |
| Verified findings | Findings emitted by the verification graph's evidence gate; this does not mean the labels judge them correct |
| Tool calls | Mean logical repository calls, including invalid requests; null when a failed graph leaves partial counts unknown |
| Latency | Mean elapsed wall time per attempted case/mode, including failures and client setup/cleanup |
| Tokens | API-reported input plus output tokens, including cached input and reasoning output |
| Cost | Estimated USD using supplied per-million rates; null without pricing or complete usage |

Reports include attempted/completed/failed counts, the completed buggy/clean
denominators, package versions, graph limits, raw findings, evaluations, and tool
evidence. Failed reviews are excluded from quality denominators and never treated
as correctly reviewed clean PRs. Compare completion counts alongside quality.
Failures record only exception type; unavailable usage and partial tool counts
are null. OpenAI's internal retries and billing adjustments are not observable,
so estimates are not an invoice. Output tokens are priced as reported by the SDK.

Supply all three current rates with `--input-rate`, `--cached-input-rate`, and
`--output-rate` in USD per million tokens. No rates are assumed or hardcoded.
Repeat live runs to assess variability. Before drawing production conclusions,
expand the dataset with pinned, independently adjudicated real PRs, varied
languages, larger repositories, and execution-supported cases.

Run regression checks with:

```powershell
uv run python -m unittest discover -s tests -v
```
