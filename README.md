# Code review agent

FastAPI and LangGraph review GitHub pull requests using structured GPT-6 Luna
outputs and repository evidence.

## Stage 15: Local deployment

Build the React UI once, then serve it and the API together on loopback:

```powershell
cd frontend
npm ci
npm run build
cd ..
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv run python -m app.scripts.run_api --serve-ui --port 8001
```

Open http://127.0.0.1:8001. The built UI calls the API on the same origin;
no Vite process or proxy is required. Configure `.env` as described below.
The launcher binds only to `127.0.0.1` and requires development settings.
Stop it with Ctrl+C. Rebuild after frontend changes and restart after backend
changes. `/health`, `/ready`, and `/docs` remain available. Only `frontend/dist`
is served, and missing files/API paths return 404.

Local operation does not require Docker or a database. Reviews are not
stored: each result lives only in the open page. Execution
remains disabled unless explicitly configured. Hosted deployment is deferred;
the Linux deployment foundation below is available for a later rollout.

## Stage 13: Deployment foundation

`deploy/` now provides a portable Linux Compose deployment for the built React
UI, an HTTPS/authenticated Caddy gateway, and a protected FastAPI service.
A separate authenticated Linux worker executes bounded snapshots with
the existing Docker isolation; the API has no Docker access in production.
Production configuration fails startup without access control or an HTTPS origin.

See the [deployment guide](deploy/README.md) for configuration, launch, worker
setup, smoke tests, and updates. This is
deployment preparation: no cloud resources have been created and no live host
has been deployed. Docker/HTTPS and live worker checks remain pending.

## Stage 12: React UI

The React workspace in `frontend/` submits PR reviews, displays findings with
links to the reviewed GitHub revision, and shows the tool evidence collected
during the review. The UI handles validation and upstream errors; repository
text is rendered as plain text.

Start the API using the local instructions below. In a second terminal, with Node.js
22.12 or newer:

```powershell
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. Vite proxies `/api` to the backend at
`http://127.0.0.1:8000`, so backend credentials stay on the server and CORS
configuration is unnecessary for local use. Reviews are synchronous: keep the
page open until completion. The UI prevents overlapping requests and does not
automatically retry submissions or claim live graph progress. If the connection
is lost or the browser is refreshed, the result is lost; start a new review.

```powershell
cd frontend
npm test
npm run build
```

The production output is `frontend/dist`. Stage 13's Caddy deployment hosts the
UI and proxies `/api` to FastAPI. `npm run preview` previews static assets
only; use `npm run dev` for the local API connection. This remains a local
development application using the backend's existing access model.

## Stage 11: Evaluation

The evaluation CLI compares diff-only review, LangGraph context retrieval, and
the full verification graph on 20 synthetic buggy PRs and 10 synthetic clean PRs.
It records findings, tool calls, latency, API tokens, and optional estimated cost.
Recall requires semantic adjudication; clean-PR false positives are counted
directly. Failed reviews are tracked separately from quality metrics.

```powershell
uv run python -m app.scripts.evaluate
```

The default runs an offline wiring check with no external calls. Use `--live`
for paid model evaluation. The fixtures use pinned local snapshots and repository
inspection tools; sandbox execution is excluded. See the
[evaluation guide](evaluation/README.md) for live runs, scoring, metric definitions,
and dataset limitations. Model quality has not yet been measured on this dataset.

The tool-calling reviewer uses the Responses API, because Chat Completions
rejects function tools with reasoning on GPT-6 Luna. Its reasoning level is set
by `verification_reasoning_effort` in `.env` (`none`, `low`, `medium`, or
`high`; default `none`). Compare levels on the verification graph before
changing the default; each report records the level used:

```powershell
uv run python -m app.scripts.evaluate --live --modes verification --verification-reasoning none --output evaluation/results/reasoning-none.json
uv run python -m app.scripts.evaluate --live --modes verification --verification-reasoning medium --output evaluation/results/reasoning-medium.json
```

## Review workflow and sandbox

The API ingests the PR and preserves its GitHub error mappings. The graph then
runs this workflow:

```text
analyse_diff → analyse_context → [retrieve_context] → form_hypotheses
                                                        ↓
                  ┌────────────────────────────── select_hypothesis
                  │                                     ↓
                  │                                  reviewer ←──────────┐
                  │                                     │                │
                  │                 tool calls ─────────┤                │
                  │                     ↓               │                │
                  │                  ToolNode           │                │
                  │                     ↓               │                │
                  │              collect_evidence ──────┼────────────────┘
                  │                                     ↓ no calls / limit
                  │                              evaluate_evidence
                  │                               ↙             ↘
                  │                        store_finding       discard
                  │                               ↘             ↙
                  └──── more hypotheses ← advance_hypothesis
                                                 ↓ no more
                                            generate_review → END
```

Each review has at most five hypotheses. The model can request `read_file`,
`search_code`, `find_references`, `run_test`, and `run_static_analysis` through
LangGraph's `ToolNode`, then inspect
their results before choosing another action. Each hypothesis has at most three
model turns and six tool calls. Inspections use the PR's exact head or base SHA.
Repository searches
find candidate paths on GitHub's default branch; context retrieval reads up to
three unique candidates at the PR head. Queries are limited to three and the
overall repository tool budget is 30 calls. Large file contents are truncated
and marked explicitly.

Tool batches are capped and counted before execution, including invalid requests.
Repository identity and SHA selection are injected from graph state; the model
only chooses a relative file path and `head` or `base`. Each hypothesis gets a
fresh message history. Unknown tools and invalid arguments produce tool errors,
and failed inspections cannot support confirmation. Search hits remain candidate
paths, not evidence of code at the PR revision.

The existing OpenAI client supplies tool calls through Chat Completions; a small
adapter converts them to LangChain `AIMessage` objects and sends `ToolMessage`
results back to the model. Structured hypotheses and evidence evaluations still
use the Responses API. See the [LangGraph ToolNode reference](https://reference.langchain.com/python/langgraph.prebuilt/tool_node)
for the tool execution and state injection APIs.

The model evaluates numbered evidence and returns a confirmed, rejected, or
inconclusive decision. Only confirmed findings with valid, successful evidence
citations and the hypothesis's location reach the final review. Rejected and
inconclusive hypotheses remain in graph state for inspection. Final review
assembly uses stored findings without another model call.

Verification includes optional Python tests and Ruff analysis in disposable Linux
Docker containers. Execution is disabled by default. Each tool downloads a bounded
GitHub ZIP at the selected head or base SHA, validates paths and file types, copies
the snapshot into a container, and removes the container and local snapshot after
execution. No repository code runs on the API host. No host directories, Docker
socket, API credentials, or environment files are mounted or forwarded.

Containers use no network, a read-only filesystem, a non-root user, dropped
capabilities, and no-new-privileges. Limits are 1 CPU, 512 MiB memory with no extra
swap, 64 processes, 64 MiB writable `/tmp`, and 60 seconds per command. Output is
bounded, snapshots allow at most 10,000 entries and 100 MiB extracted content,
and at most two executions run concurrently per API process. Docker's isolation
flags are documented in the [Docker container reference](https://docs.docker.com/reference/cli/docker/container/create/).

The model selects one existing file and `head` or `base`. The service fixes the
commands to pytest or Ruff; it never installs PR dependencies or runs shell commands.
Ruff ignores repository configuration. Pytest plugin autoload is disabled, but
tests and conftest files remain untrusted code executed inside the sandbox.
The initial image supports Python repositories using pytest and Ruff. Missing
dependencies and unsupported projects produce incomplete evidence.

Execution evidence records output, exit code, and timeout. `succeeded` means the
command completed with exit code 0 or 1, not that tests passed. Collection errors,
OOM kills, timeouts, disabled execution, and infrastructure failures cannot support
confirmation. A test failure alone does not establish a regression.
Reviews remain synchronous.

### Enable execution

Install Docker with Linux-container support, then build the trusted image:

```powershell
docker build -t code-review-sandbox:stage9 sandbox
```

Set `sandbox_enabled=true` in `.env` and restart the API. `sandbox_image` defaults
to `code-review-sandbox:stage9`; use a trusted image (preferably a digest) when
changing it. Runtime image pulling is disabled. Image builds may use network;
review executions cannot. Docker must be available to the API process. Production
deployments should use a dedicated worker host with a maintained Docker runtime;
containers share the host kernel. Host crashes require operational cleanup of
containers named `review-sandbox-*`.

After building the image, run the optional local Docker smoke test. It uses a
synthetic archive with no GitHub or OpenAI calls and checks non-root execution,
blocked networking, read-only repository files, absent credentials, and captured
test failures:

```powershell
$env:SANDBOX_SMOKE_TEST = "1"
uv run python -m unittest discover -s tests -p test_sandbox.py -v
Remove-Item Env:SANDBOX_SMOKE_TEST
```

## Run locally

Install dependencies with `uv sync`. Configure `github_token` and
`openai_api_key` in `.env`, then run:

```powershell
uv run uvicorn app.main:app --reload
```

If Windows reports `WinError 10013` or `WinError 10048` when binding the
default port, use an available port such as 8001:

```powershell
uv run python -m app.scripts.run_api --reload --port 8001
```

When using the React UI, start its development server in a second terminal
with the matching backend address (restart Vite if it is already running):

```powershell
cd frontend
$env:API_PROXY_TARGET = "http://127.0.0.1:8001"
npm run dev
```

Submit `POST /api/v1/reviews` with:

```json
{"repository": "owner/repository", "pull_request_number": 123}
```

The response contains PR metadata and the verified findings.

### LangSmith tracing

Tracing is off by default. To send each review to LangSmith, add to `.env`:

```
langsmith_tracing=true
langsmith_api_key=lsv2_...
langsmith_project=code-review-agent
```

Set `langsmith_endpoint` as well for a self-hosted or EU instance. Each API
review appears as a `pull_request_review` trace tagged with the repository and
PR number, with a child run per graph node, tool call, and OpenAI request
(including token usage). Live evaluation runs are tagged `evaluation` with the
case ID and mode; offline wiring checks are never traced. Traces contain PR
diffs and repository content, so only enable this for repositories you may
share with LangSmith.

Run mocked graph tests (no external API requests):

```powershell
uv run python -m unittest discover -s tests -v
```
