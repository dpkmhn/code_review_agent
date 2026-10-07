Yes — then I’d **change the plan to make LangGraph the orchestration layer from the start of the agentic part**. That fits this project very well 🔥

The project already naturally maps to a graph:

```text
Fetch PR
  ↓
Analyse diff
  ↓
Retrieve context
  ↓
Form hypotheses
  ↓
Verify
  ↓
Collect evidence
  ↓
Generate review
```

That is basically a LangGraph workflow.

## Revised plan with LangGraph

### Stage 1 — FastAPI + GitHub ingestion
Keep this unchanged.

Build:

```text
POST /reviews
GET /reviews/{id}
```

And tools:

```python
get_pr()
get_pr_diff()
```

At the end of this stage, you have:

```text
GitHub PR
  ↓
PR metadata + diff
```

### Stage 2 — GPT-6 Luna baseline review
Use GPT-6 Luna only.

Flow:

```text
PR diff
  ↓
GPT-6 Luna
  ↓
Structured findings
```

This gives you a baseline before adding agent orchestration.

The source makes clear that an initial suspicious observation should remain a hypothesis rather than immediately becoming a confirmed bug. Pasted text

### Stage 3 — LangGraph fundamentals 🧠
Now introduce LangGraph.

Define shared state:

```python
class ReviewState(TypedDict):
    repo: str
    pr_number: int

    pr_data: dict
    diff: str

    changed_files: list[str]
    relevant_context: list[str]

    hypotheses: list[dict]
    evidence: list[dict]

    findings: list[dict]

    tool_calls: int
```

Initial graph:

```text
START
  ↓
fetch_pr
  ↓
analyse_diff
  ↓
END
```

This stage is intentionally simple.

Goal: understand:

```text
State
Nodes
Edges
Graph execution
```

before adding complex routing.

### Stage 4 — Repository investigation tools 🔎
Add:

```python
read_file()
search_code()
find_references()
```

Then graph becomes:

```text
START
  ↓
fetch_pr
  ↓
analyse_diff
  ↓
need_context?
  ├── YES → retrieve_context
  │            ↓
  │       analyse_context
  │            ↓
  │        need_context?
  │
  └── NO
       ↓
form_hypotheses
```

This matches your project design closely: identify changed symbols and retrieve only relevant surrounding code rather than stuffing the entire repository into context. Pasted text

### Stage 5 — Hypothesis-driven review
Create explicit hypotheses:

```python
class BugHypothesis(BaseModel):
    description: str
    file: str
    line: int
    confidence: str
    verification_plan: list[str]
```

LangGraph state now carries:

```python
hypotheses: list[BugHypothesis]
```

Flow:

```text
analyse_context
      ↓
form_hypotheses
      ↓
select_hypothesis
```

Example:

```text
Hypothesis:
Removed null validation may allow None into process_payment()

Verification plan:
1. inspect repository return type
2. inspect process_payment()
3. find tests
4. reproduce failure
```

Your source explicitly recommends a **bounded set of hypotheses**, which maps nicely to graph-controlled state. Pasted text

### Stage 6 — Verification loop 🧪
Now add:

```python
find_tests()
run_test()
run_static_analysis()
```

Graph:

```text
select_hypothesis
        ↓
choose_verification
        ↓
 ┌──────┼──────────┐
 ↓      ↓          ↓
test   static    inspect
 ↓      ↓          ↓
 └──────┴──────────┘
        ↓
collect_evidence
        ↓
verified?
   ┌────┴────┐
  YES       NO
   ↓         ↓
finding    discard
```

This is where LangGraph becomes really useful because the control flow is no longer linear.

The project design itself revolves around this idea:

```text
claim
+
evidence
+
verification
```

Pasted text

### Stage 7 — Build the full review graph
At this point, your graph can look like this:

```text
START
  ↓
fetch_pr
  ↓
analyse_diff
  ↓
retrieve_context
  ↓
form_hypotheses
  ↓
select_hypothesis
  ↓
verification
  ↓
evaluate_evidence
  ↓
finding?
 ┌───────┴────────┐
YES               NO
 ↓                 ↓
store_finding    discard
      \           /
       ↓         ↓
   more_hypotheses?
      ┌────┴────┐
     YES        NO
      ↓          ↓
select_next   generate_review
                   ↓
                  END
```

This is essentially your source's agent loop represented explicitly as a graph. Pasted text

## Where GPT-6 Luna fits

Important distinction:

**LangGraph is not the intelligence.**

Use:

```text
LangGraph
= workflow / state / routing

GPT-6 Luna
= reasoning / decisions / structured output
```

Example:

```text
LangGraph node:
analyse_diff

        ↓

GPT-6 Luna:
"What changed and what looks risky?"
```

Then another node:

```text
choose_verification
```

Luna decides:

```text
run_test()
```

but LangGraph controls where the workflow goes next.

That separation is excellent architecture for this project.

## Stage 8 — ToolNode + conditional routing

Implemented: the verification loop uses a reviewer node, conditional routing,
ToolNode, and evidence collection. Available tools are read_file, search_code,
and find_references, scoped to the current PR. The loop is bounded to three model
turns and six tool calls per hypothesis, with a 30-call review budget. Test and
static-analysis execution tools remain deferred until the stage 9 sandbox.
Once the basic graph works, introduce proper LangGraph tool calling.

Conceptually:

```python
llm_with_tools = llm.bind_tools([
    read_file,
    search_code,
    find_references,
    find_tests,
    run_test,
    run_static_analysis,
])
```

Then:

```text
reviewer_node
      ↓
tool requested?
 ┌────┴────┐
YES       NO
 ↓         ↓
ToolNode   next stage
 ↓
reviewer_node
```

So part of the graph becomes:

```text
GPT-6 Luna
     ↓
tools?
 ┌───┴───┐
 yes     no
 ↓        ↓
ToolNode  continue
 ↓
GPT-6 Luna
```

🔥 This is the part I’d spend the most time understanding.

## Stage 9 — Safe execution sandbox
Implemented: optional Python pytest and Ruff ToolNode tools use a disposable
Docker container at the exact selected PR head or base revision. Execution is
disabled by default and requires a prebuilt trusted image. Containers have no
network or credentials, run as non-root with a read-only filesystem and dropped
capabilities, and enforce CPU, memory, process, output, and timeout limits.
Validated snapshots and containers are cleaned up after each invocation.
Mocked security, failure, and graph integration tests cover this stage; live
Docker verification is pending on a machine with Docker available. Python
dependency installation and other language runners are not part of this stage.

Your LangGraph tools should never execute arbitrary repository code directly.

Instead:

```text
LangGraph
    ↓
run_test tool
    ↓
Sandbox Service
    ↓
Disposable Docker container
```

Sandbox:

```text
timeout
CPU limit
memory limit
network restrictions
no credentials
temporary filesystem
```

Then destroy it after review.

Your source explicitly calls isolation a critical requirement because the repository code is untrusted. Pasted text

## Stage 10 — Persistence + checkpointing
Removed on 2026-10-07: PostgreSQL storage, LangGraph checkpointing, and the
lookup/resume endpoints were taken out of the project. Reviews are transient;
`POST /api/v1/reviews` returns findings and tool evidence directly. The notes
below describe the removed implementation.

Previously implemented: optional PostgreSQL tables persist review runs, findings, tool-call
records, and sandbox executions. LangGraph uses AsyncPostgresSaver with a stable
review UUID, synchronous checkpoints, and allowlisted application model types.
Lookup and explicit resume endpoints recover failed/interrupted runs at the
original PR SHAs. Advisory locks prevent duplicate execution across workers,
and idempotent projections recover the final result after a checkpoint/commit
gap. Live PostgreSQL tests cover recovery with a new pool, locking, findings,
and execution evidence; GitHub/OpenAI calls and sandbox execution are mocked.
On Windows, persistence uses a Selector event loop and sandbox execution stays
disabled; running both requires Linux. Automatic background recovery, retention,
authentication, and subsequent schema migrations remain future work.

This is another reason LangGraph is a good choice.

Add PostgreSQL for:

```text
review_runs
findings
tool_calls
executions
```

Then later add LangGraph persistence/checkpointing.

That gives you:

```text
review starts
 ↓
graph executes
 ↓
checkpoint
 ↓
failure/server restart
 ↓
resume graph
```

This is a strong production-level feature to discuss in interviews.

## Stage 11 — Evaluation 📊
Implemented: an evaluation CLI and 30 pinned synthetic Python PR fixtures (20
buggy, 10 clean) compare diff-only, context retrieval, and inspection-based
verification using the existing single-model services. Reports record quality,
completion/failure counts, tool calls, latency, tokens, and optional cost rates.
Semantic human adjudication is required for bug matches; location overlaps are
only hints. Offline wiring checks suppress quality scores and make no external
requests. Live model evaluation, real-world PR curation, repeated measurements,
and sandbox-supported evaluation remain pending. See evaluation/README.md.

Build your test dataset:

```text
20 buggy PRs
10 clean PRs
```

Then evaluate:

```text
Bug recall
False-positive rate
Verified findings
Clean-PR accuracy
Tool calls
Latency
Tokens
Cost
```

These are the same metrics already suggested in the project source. Pasted text

And because you're using one model only:

```text
GPT-6 Luna
```

you can evaluate architecture changes cleanly:

```text
Diff-only
vs
LangGraph + context retrieval
vs
LangGraph + verification
```

That is actually more interesting than comparing models.

## Stage 12 — React UI

Implemented: a local React/Vite workspace submits GitHub PR reviews, displays
verified findings and changed files, and links findings to the exact reviewed
head revision, with the tool evidence returned by the review. The UI escapes
repository/tool text. (Saved-review lookup and resume were removed with
PostgreSQL on 2026-10-07.) Synchronous requests have a waiting state and prevent duplicate submission;
there is no invented graph progress or automatic retry. Component tests mock
API requests for submission, validation, evidence, and error handling.
Production hosting remains stage 13; real-model end-to-end UI evaluation
remains pending.

## Stage 13 — Deployment

Current decision: keep the application local. Hosted rollout and a separate Linux
worker are intentionally deferred; the deployment foundation remains available
for a later rollout. Local development continues without sandbox
execution unless explicitly configured.

Deployment foundation implemented: Linux Compose builds/hosts the React UI behind
an HTTPS Caddy gateway with shared workspace authentication, protects review API
routes with a server-only bearer token and browser origin checks.
Production execution dispatches bounded exact-revision archives to an authenticated
separate Linux worker with no upstream credentials. Deployment examples,
systemd worker setup, a synthetic isolation smoke CLI, and update runbooks are
in deploy/. Offline auth/worker/regression tests and the frontend
build are verified. Live deployment, Docker/Caddy/Compose validation, private-network configuration, and end-to-end review/worker checks
remain pending until a Linux hosting target, domain, and credentials are supplied.
Per-user isolation, cloud provisioning, scheduling, and operational monitoring
remain future work.

## Final build order I recommend 🎯

Use this 15-item build order when referring to numbered stages. The detailed
sections above group the work into 13 sections, so their numbers differ from
this checklist. Stage 14 is the React UI, documented in the Stage 12 section
above and implemented in `frontend/`. Stage 15 is deployment, documented in
the Stage 13 section above; hosted rollout remains deferred. The current
stage 15 target is local deployment: the built React UI and API run together
on loopback using `app.scripts.run_api --serve-ui`, without a Vite process.
See README.md for build, launch, and restart instructions. Docker
and paid model end-to-end validation remain separate optional checks.

Stage 15 local status: implemented and validated on 2026-10-03. Backend tests
passed (70 across the existing suite and local UI checks; three optional live
Docker/PostgreSQL tests skipped). Four frontend tests and the production build
passed. A running loopback server passed HTTP smoke checks for the built UI and
assets, health/readiness, docs, API validation, missing paths, and disabled
persistence. Live GitHub/OpenAI review submission was not part of this smoke.

Stage 14 status: implemented. The four frontend component tests and production
build passed on 2026-10-03. Live model end-to-end UI validation remains pending
as described above.

1. **FastAPI foundation**
2. **GitHub PR ingestion**
3. **GPT-6 Luna structured baseline**
4. **LangGraph StateGraph basics**
5. **Repository tools**
6. **Context retrieval nodes**
7. **Hypothesis generation**
8. **Verification nodes/tools**
9. **Conditional routing**
10. **Full review loop**
11. **Docker sandbox**
12. **PostgreSQL + graph persistence**
13. **Evaluation framework**
14. **React UI**
15. **Deployment**

For this project, **LangGraph is a good choice** because the workflow genuinely has branching, loops, shared state, tool calls, and verification steps. You're not forcing a framework onto a simple prompt chain; you're using it where orchestration actually matters. 🔥
