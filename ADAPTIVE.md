# Adaptive routing and verification

All features use the external Markdown store (`~/codex-sidekick-data` by default).
The old SQLite database is left as an archive and is not imported. Configuration
and prompt files are snapshotted per run; changing source files affects newly
submitted runs, not queued or active ones.

## Discovery before planning

Automatic planning now begins with an orchestrator-authored discovery brief.
Python launches the investigations as independent read-only sessions, persists
their reports and file-hash evidence in the Markdown store, and supplies the results to the
planning orchestrator. Roles `discovery_brief`, `discovery`, and `orchestrator`
keep the phase boundaries visible in status, results, and metrics. Discovery
evidence is stored in the `discovery_evidence.md` session artifact.

The default `discovery` settings enable the stage, require at least one
investigation, suggest Sol medium to the orchestrator,
allow two concurrent investigations per round (also bounded by `max_parallel`),
and allow one follow-up round. Every investigation must explicitly choose model
and effort; the suggested route never fills in missing choices. `allow_skip: true` permits an explicit skip with
a reason when initial context is sufficient. `enabled: false` bypasses discovery.

The planner can return `discovery_requests` plus `discovery_reason` to ask Python
for follow-up investigation. It must leave `sidekicks` empty until those findings
arrive. The next planning invocation receives all discovery reports and failures
without their transcripts. The same logical orchestrator has separate recorded
attempts/threads. Exhausting the follow-up limit fails explicitly instead of
launching premature implementation. If every investigation fails, a final plan
is blocked; partial failures remain visible for the planner to assess.

Discovery workers reuse checkpoint/effort escalation, but return checkpoints in
their final report because their sandbox has no writable checkpoint inbox. They
cannot request repairs or run host verification commands. Discovery calls count
toward the same attempt/time/observed-token budgets as all other phases. Read-only
discovery outcomes are not treated as verified implementation samples by tuning.

`discover --brief FILE` exposes this phase to an existing initial conversation.
That conversation receives the results and creates its plan; Python remains the
only launcher. `submit --plan FILE` and single-session `launch` keep their existing
behavior, since they do not contain an automatic planning stage. See the README
for commands and `examples/discovery-brief.json` for a two-worker brief.

## 1. Checkpoints and evidence-driven escalation

Every worker attempt gets an isolated checkpoint inbox. Its prompt supplies the
path and schema. Between meaningful work steps, it can atomically write
`checkpoint.json` to that inbox:

```json
{
  "status": "needs_escalation",
  "completed": ["Implemented the parser change"],
  "evidence": ["12 cases passed; two ambiguous cases failed"],
  "failed_tests": ["test_nested_behavior"],
  "blocker": "Requirements imply two different behaviors",
  "remaining_work": ["Resolve behavior", "Fix the remaining cases"],
  "suggested_model": "astra",
  "suggested_effort": "high"
}
```

All fields are required. Empty strings/lists are valid when appropriate.
`progress` records a checkpoint and continues. `yield` requests a bounded
continuation at the current route. `needs_escalation` requests a stronger route.
Use a temporary file followed by rename, or the provided helper:

```sh
python3 /path/to/router.py checkpoint --inbox /provided/inbox --file /path/to/checkpoint.json
```

Python polls the inbox, validates and persists each update in the Markdown store, and
monitors repeated failed-test signatures and lack of checkpoint progress.
On handoff, it stops the process group, preserves the worktree, and launches the
next attempt with the checkpoint, verification evidence, and prior compact
report. Workers must checkpoint between tool actions and stop issuing tools
after requesting a handoff. This is cooperative progress reporting, backed by
stall and timeout enforcement; the router does not inspect private reasoning.

A worker can instead return its checkpoint in its final report with status
`checkpoint` (for yield) or `needs_escalation`. This also works in read-only
sessions that cannot write an inbox. Normal reports use `checkpoint: null`.

The configured escalation ladder defaults to Luna medium → Luna high → Sol high
→ Astra high. Suggestions can select a stronger configured step but cannot
downgrade or inject a model. A current effort above a ladder step is never
downgraded. At the top or attempt limit, unfinished work goes to final review.

Network/rate-limit failures get a bounded retry on the **same** route. Model
errors, stalls, and failed executable checks can escalate. Recognized auth/model
configuration errors stop retries. These categories use explicit outcomes and
conservative error-text matching; an unfamiliar provider error may be classified
as a model failure. All routing decisions and failure categories are recorded.

Default bounds in `adaptive`:

| Setting | Default |
| --- | --- |
| `max_attempts` | 3 useful attempts per assignment |
| `max_infrastructure_retries` | 1 additional infrastructure retry |
| `max_repair_rounds` | 2 |
| `max_repairs_per_round` | 3 |
| `max_total_attempts` | 30 across planning, workers, repairs, and reviewers |
| `max_run_seconds` | 14,400 |
| `max_observed_tokens` | 1,000,000 reported input + output tokens |
| `stall_seconds` | 600 without changed checkpoint progress |
| `repeated_failure_limit` | 2 repeated failed-test checkpoints |

The total-attempt claim is atomic across concurrent workers. Reported token
usage is an observed threshold, not a hard provider spend limit: concurrent or
aborted calls may consume tokens not yet reported. Set the stall deadline above
expected long-running operations. A budget exhausted before review yields a
failed/incomplete run, never a claim of successful completion.

## 2. Route by judgment, uncertainty, and verification needs

Each assignment can include:

```json
{
  "task_type": "refactor",
  "assessment": {
    "ambiguity": 0,
    "judgment": 1,
    "failure_impact": 1,
    "verification_difficulty": 1,
    "mechanical": 3
  }
}
```

Scores range from 0 to 3 and help the orchestrator explain its choice. Every
assignment also requires explicit `model` and `effort` fields selected from the
catalog. Python validates and preserves that initial choice even for high-judgment
tasks. `routing` suggestions and measured `task_type_routes` are advisory; neither
assessment nor legacy `complexity` can supply a missing model or effort. The old
`routing_policy.enforce_frontier_floor` setting no longer overrides selections.
After an unsuccessful attempt, the existing bounded escalation ladder still
selects the retry route. The configured orchestrator/reviewer roles are unchanged.

## 3. Executable acceptance checks

Add trusted check definitions to your JSON configuration:

```json
{
  "checks": {
    "unit": {
      "argv": ["python3", "-m", "unittest", "discover", "-s", "tests"],
      "cwd": ".",
      "timeout_seconds": 300
    }
  }
}
```

Assignments and the plan select names, for example:

```json
{
  "checks": ["unit"],
  "acceptance_criteria": ["Regression is covered", "Existing tests still pass"]
}
```

Python executes the configured argv directly, without a shell, in the worker's
worktree after its report. It records exit status, elapsed time, and the last
12 KB of combined output separately in `verification`. Nonzero exits and
timeouts invalidate a completed claim and trigger bounded repair/escalation.
Missing executables or other command-start errors are configuration failures.
The final reviewer must also pass the union of plan, worker, and repair checks.

Commands run as the local Python process, **outside Codex's sandbox**. Their
definitions are trusted operator configuration; agent plans can only reference
existing check names. Verification commands are disabled in read-only mode.
No universal checks are enabled by default because projects use different test
systems. With no checks, completion is explicitly a model-reported outcome and
`status` shows `verification_status: "not_configured"`; it does not qualify as
verified evidence for tuning. Natural-language acceptance
criteria guide the agents; they are not magically executable proofs.

## 4. Reviewer-directed repairs

The reviewer can return `status: "needs_repair"` and a nonempty `repair_requests`
array. Each request has `id`, `task`, `context`, `model`, `effort`, `rationale`,
`task_type`, `assessment`, `checks`, `acceptance_criteria`, and `context_keys`.

Python validates the requests, launches the repairs in isolated worktrees based
on the reviewer's complete current patch, and then starts another **fresh**
frontier reviewer. Repair patches contain only their incremental changes.
Conflicts are visible rather than overwritten. Review rounds are bounded; an
unresolved request at the limit returns incomplete. Reviewers retain authority
over acceptance and can make their own necessary corrections. They never spawn
workers directly, and their transcripts are not passed to the next reviewer.

## 5. Selective shared context with freshness checking

Plans can declare:

```json
{
  "context_records": [
    {
      "key": "api-contract",
      "kind": "constraint",
      "content": "Keep the public function signature unchanged",
      "files": ["src/api.py"]
    }
  ],
  "required_context_keys": ["api-contract"],
  "reviewer_context_keys": ["api-contract"]
}
```

Kinds are `fact`, `decision`, `constraint`, and `evidence`. Each worker's
`context_keys` selects additional records. Required keys are automatically
included in every worker/repair/reviewer. Python stores source SHA-256 hashes
and the base commit; when it materializes context in an updated worktree, it
flags changed/missing files as stale and instructs the agent to revalidate them.
Freshness cannot automatically prove a prose fact correct. Unselected records
are not copied into prompts. This avoids duplicating large shared context in
every assignment. The reviewer still receives the selected initial context and
all final handoff reports, as required by the workflow.

```sh
python3 router.py context RUN_ID --keys api-contract --workspace /path/to/worktree
```

Records are immutable per run. Read-only runs can use non-Git folders; they
still use file hashes where records reference files. External/symlink paths
escaping the workspace are rejected.

## 6. Measure quality and adjust routing

```sh
python3 router.py metrics
python3 router.py metrics --task-type refactor
python3 router.py --config config.json tune --min-samples 5 --output tuned.json
python3 router.py --config tuned.json submit ...
```

Metrics group attempts by task type, model, and effort. They include completion,
independently verified completion, retries, infrastructure failures, resumed
turns, observed tokens, elapsed time, runs needing repairs, and reviewer-changed
lines. Reviewer edits are a **run-level** signal and can include legitimate
integration work; they are not attributed as a specific worker's mistakes.
Git's text-line counts do not measure semantic quality or binary changes.

`tune` writes a new configuration without modifying the original. It requires
at least the requested number of distinct clean, verified runs for a route and
at least 90% attempt completion. Among qualifying routes it selects the lowest
mean observed token count for that task type. "Clean" means final completion,
passing recorded checks, no repair workers, and no measured reviewer file
changes (including binary files). It ignores unverified claims and small samples.
The planner sees measured preferences as advice and must still choose an explicit
model and effort for each initial assignment.

Token count is a transparent tuning heuristic, **not dollar-cost optimization**;
models have different prices. Evaluate recommendations on representative tasks
before broadly adopting them. Existing runs always retain their original config.

## Optional independent thread reuse

Set `adaptive.reuse_sidekick_threads` to true to reuse a sidekick's own Codex
thread for same-route continuations. Python passes its explicit stored thread
ID to `codex exec resume`. There is no `--last`, no orchestrator fork, and no
cross-worker transcript reuse. Model/effort changes, infrastructure retries, and
reviewer attempts start fresh. Different-model switches occur at explicit
checkpoint/attempt boundaries, not by intercepting Codex compaction. Cache hits
are measured when reported, never guaranteed by the Markdown store or thread reuse.

Command providers receive `resume_thread_id` and `checkpoint_path` in their stdin
envelope. To opt into reuse they must declare `supports_resume: true` and honor
the exact supplied ID. Otherwise each invocation remains independent.
