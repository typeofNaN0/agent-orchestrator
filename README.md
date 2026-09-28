# Codex Orchestration / Router

A dependency-free Python runner for **independent Codex sessions with Markdown
handoffs**. Discovery informs the orchestrator's plan; Python launches; a fresh frontier
reviewer integrates and finishes. No transcript forks or native Codex subagents.

```text
              Initial task + selected context
                            |
             Orchestrator writes discovery brief
                            |
             Python launches read-only discovery
                            |
               Findings + evidence → Markdown
                            |
             Orchestrator plans and selects routes
               (bounded discovery follow-up)
                            |
                      Python scheduler
                            |
               +------------+------------+
               |            |            |
           Luna medium   Sol high     Astra high
           new thread    new thread   new thread
               |            |            |
               +---- reports + patches --+
                            |
                          Markdown
                            |
                Fresh Astra reviewer thread
           original task + reviewer prompt + handoffs
                            |
                completed worktree + final patch
```

The model in the first conversation does not call a session-launching tool. It
writes an explicit plan and passes it to this script. Python executes `codex exec`
with the selected model and effort. Fresh threads are the default; optional
sidekick continuation can resume that sidekick's own thread. It never forks the
orchestrator or invokes a native subagent. User configuration is not inherited by child Codex sessions;
saved CLI authentication is reused. The router disables native multi-agent
features in each child invocation.

Version 0.4 replaces the shared database with an external Markdown store and adds
snapshotted prompt-file inputs. The orchestrator explicitly chooses every initial
model and effort; the existing retry ladder still handles failures. Read-only
discovery precedes automatic planning and supports bounded follow-up investigations.
The existing adaptive features include checkpoint-driven escalation, orchestrator-selected initial routes,
independent verification commands, reviewer-directed repair rounds, selective
shared context with freshness checking, and measured routing adjustments.
See [ADAPTIVE.md](ADAPTIVE.md) for configuration and examples.

## Requirements

- Python 3.9+ (standard library only).
- Git for editing runs, with a clean, committed repository.
- A Codex CLI supporting `exec --ignore-user-config --json --output-schema`
  and the configured models/efforts. Command construction was checked against
  local Codex CLI 0.157.1. Account model availability is separate from API docs.
- Existing CLI login: `codex login`. No separate API key is required when using
  an authenticated Codex CLI account.

Run the script directly; installation is optional. Commands below assume your
shell is in this project. Use an absolute `router.py` path from other directories.

## Launch one session

```sh
python3 router.py launch \
  --workspace /absolute/path/to/repo \
  --task 'Implement the bounded change described in the context' \
  --context-file /absolute/path/to/context.txt \
  --model luna --effort medium
```

`launch` starts a detached Python runner and returns a run ID immediately. It
creates one logical assignment, with **no extra planner or reviewer**; adaptive
retries can start additional attempts/threads for that assignment. Use
`--wait` to run in the foreground. For analysis with no edits and no Git
requirement, add `--read-only`.

## Run your orchestration workflow

Use [ORCHESTRATOR.md](ORCHESTRATOR.md) as the initial thread's instructions. Write
a plan like [examples/plan.json](examples/plan.json), replacing its placeholder
context. Keep plan/context files outside the target repo so it stays clean.

```sh
python3 router.py submit \
  --workspace /absolute/path/to/repo \
  --task 'The complete original user request' \
  --context-file /absolute/path/to/context.txt \
  --plan /absolute/path/to/plan.json \
  --start
```

This runs the supplied sidekicks, then automatically starts a **new Astra
reviewer session** with the orchestrator's reviewer prompt and all sidekick
results. The initial conversation does not need to stay active. Without
`--start` or `--wait`, `submit` only stores the run; launch it later with
`python3 router.py start RUN_ID`.

To have Python launch the initial planning session too, omit `--plan`:

```sh
python3 router.py submit --workspace /absolute/path/to/repo \
  --task 'Implement this feature and verify it' --start
```

Python first runs an orchestrator session to write a discovery brief, then
launches independent **read-only discovery sessions** with the orchestrator’s
explicit model and effort choices (Sol medium is the suggested discovery route).
Their structured findings and file hashes go into Markdown. Only then does the
planner choose sidekick count/model/effort/context, dependencies, and reviewer
instructions. It can request one bounded follow-up discovery round. Each
planning invocation starts fresh with explicit findings, not worker transcripts.
Python validates its plan
before executing it. A tiny task can have zero sidekicks and go straight from
planning to the reviewer.

Discovery is enabled by default for automatic planning. In `config.json`,
`discovery.allow_skip` permits the briefing orchestrator to skip when context is
sufficient; `discovery.enabled: false` disables the stage. An explicitly supplied
`--plan` is already planned and executes directly without late discovery.

## Discovery for an existing orchestrator conversation

Before writing an execution plan, the current Codex conversation can submit a
[discovery brief](examples/discovery-brief.json):

```sh
python3 router.py discover --workspace /absolute/path/to/repo \
  --task 'The original user request' \
  --context-file /absolute/path/to/context.txt \
  --brief /absolute/path/to/discovery-brief.json
python3 router.py result RUN_ID
```

`discover` starts Python in the background by default; use `--wait` for foreground
execution. A supplied brief avoids another model call to generate it. Without
`--brief`, Python launches a briefing orchestrator first. No implementation or
reviewer sessions run in discovery-only mode. The current conversation reads
the returned reports, decides the plan, and submits it with `submit --plan`.
Add `--read-only` for analysis of a non-Git directory. Discovery always uses a
read-only agent sandbox, including when it inspects isolated Git worktrees.

Discovery reports identify relevant files/symbols, relationships, existing
behavior, conventions, candidate checks, dependencies, risks, and unresolved
questions. Python fingerprints cited files and passes the evidence to planning.
Candidate test commands are suggestions, not host-executed checks. Findings
are agent claims supported by references, not independent semantic proof. All
failed investigations are visible; planning cannot finalize when none completed.

## Retrieve the result

```sh
python3 router.py status RUN_ID
python3 router.py result RUN_ID
python3 router.py plan RUN_ID
python3 router.py export RUN_ID --output /absolute/path/to/final.patch
```

`status` returns small metadata and token usage, without full reports. `result`
adds reports and the final reviewer's worktree path. `export` writes the complete
diff relative to the original commit, including new unignored files. A failed or
incomplete run may have a useful partial patch; its status is always disclosed.

Editing runs leave the original checkout untouched. Work happens in retained,
detached Git worktrees inside the external data directory. Inspect the final reviewer worktree
or apply the exported patch to a suitable checkout yourself:

```sh
git -C /absolute/path/to/repo apply --check /absolute/path/to/final.patch
git -C /absolute/path/to/repo apply /absolute/path/to/final.patch
```

The router does not automatically merge, commit, push, or publish. Follow-up
work can target the completed worktree after you commit its changes. Ignored
files, virtual environments, and uncommitted source changes are not copied into
worktrees. Submodules are currently rejected. Set up dependencies as required
by the project, or use read-only mode for analysis of the original checkout.

## Markdown is the handoff mechanism

Default location: `~/codex-sidekick-data`, separate from the router source and
project workspace. Override it with `--store-dir /absolute/path/data` **before**
the subcommand, and use the same option for later status/result commands.
`status` and `result` return `store_dir` instead of the former `database` field.

```text
codex-sidekick-data/
  runs/RUN_ID/
    run.md, task.md, context.md, config.md, plan.md, sources.md
    sources/HASH.md
    claim/owner.md
    context/HASH.md
    events/EVENT_ID.md
    sessions/SESSION_ID/
      session.md, spec.md, prompt.md, report.md
      artifacts/patch.patch, final.patch, integration.md, ...
      attempts/ATTEMPT_ID/
        attempt.md, prompt.md
        checkpoints/CHECKPOINT_ID.md
        verification/CHECK_ID.md
  logs/RUN_ID/SESSION/
  worktrees/RUN_ID/SESSION/
```

Files are created as needed. Metadata is stored in versioned fenced JSON inside
Markdown documents, with readable text sections. Metadata blocks are authoritative;
text sections and the separate task/config/plan/report views are for inspection.
Each attempt's `prompt.md` is the exact prompt sent to the provider. JSON CLI
output and provider protocols remain unchanged, except for the storage field.
Patches, raw provider logs, responses, schemas, and checkpoint inboxes retain
their native formats. Sidekicks never write scheduler records directly.

Updates use a temporary file in the destination directory, fsync, and atomic
rename. Events and checkpoints have individual files. Fully initialized runs
are published together; unpublished staging directories are ignored by readers.
An exclusive persistent directory claim assigns each run to one process. Published
runs require ownership before any scheduler write. A
short in-memory lock per run protects thread updates and attempt-budget checks;
independent runs share no global write lock or database. This supports parallel
workers and router processes on **one Mac using local disk**. A synced directory
is not a distributed execution queue.

Background execution survives the submitting CLI exiting. An interrupted claim
or unexpectedly killed runner is surfaced in status and **never automatically
replayed**. Inspect logs/artifacts and submit a new run with explicit recovery
context. Do not remove a claim to restart an old run: its worker effects may
already exist. Malformed records cause explicit errors instead of being skipped.

This is a fresh store. The old
`~/.local/share/codex-sidekick-router/state.sqlite3` and its logs are left untouched;
there is no importer, and old run IDs do not appear in the new store. `--db` is
replaced by `--store-dir`. Existing processes running the old version should
finish against their original store before being retired.

Raw CLI events and stderr live under `logs/RUN_ID/SESSION/`, with later attempts
under `attempt-N/`. They are not sent to the reviewer. Codex also maintains its
own session storage; replacing the router's database does not change Codex's
internal storage. Worktrees, records, and logs are retained with no automatic
deletion policy.

## Saved prompts and token caching

Use `--task-file` instead of `--task` to load a saved UTF-8 Markdown task. The
existing `--context-file` also accepts Markdown. For example:

```sh
python3 router.py launch --workspace /absolute/path/to/repo \
  --task-file /absolute/path/to/prompts/task.md \
  --context-file /absolute/path/to/prompts/context.md \
  --model sol --effort high --wait
```

Supplied JSON plans and discovery briefs support `task_file` and `context_file`
on each assignment/investigation. Plans also support `reviewer_prompt_file`.
Use only one of an inline field and its corresponding file field, even when the
inline value would be empty. See `examples/file-plan.json` and its prompt files.
CLI paths resolve from the invocation directory; references inside a plan/brief
resolve from that file's directory. Ordinary source citations in context records
still resolve from the workspace.

Python reads files once, applies the existing text limits, and snapshots their
contents, resolved paths, and SHA-256 hashes during submission. Later edits to
source files cannot change queued work. Internally generated assignments remain
structured model outputs and are saved to the same Markdown store. Source paths
and hashes are audit metadata, not extra text injected into model prompts.

The router loads saved text directly and puts common instructions, the original
task, and selected shared references before role-specific tasks and changing
checkpoint paths. Stable serialization and unchanged snapshots make the portion
of the prefix controlled by the router repeatable. Shared context remains
selective: a sidekick never inherits the orchestrator's full conversation.

A file path is not a token-cache handle. Reusing generated text saves authoring
work, but does not transfer the orchestrator's model computation to a sidekick.
OpenAI cache reuse depends on matching rendered prefixes and compatible settings,
including model, tools, effort, and output schema. Codex also adds its own context;
different worktrees and session roles may limit reuse. These prompt changes give
more control over repeated input without guaranteeing cache hits. Check the
provider-reported `cached_input_tokens` in `status`, `result`, and `metrics`.
See [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

## Context and integration rules

- Each sidekick gets the original task, its own selected context, and transitive
  dependency reports. It never gets the original conversation transcript or
  unrelated sidekick reports. Copy relevant user constraints into its context.
- The reviewer gets the original task, initial context, reviewer instructions,
  every sidekick report/error, and workspace references. It can inspect files
  without loading raw transcripts. Reports are treated as unverified evidence.
- Dependency worktrees receive upstream patches before execution. Their own
  patches contain only incremental changes, so shared ancestors are not applied
  twice. Ready assignments run in parallel, currently in dependency waves.
- A failed/incomplete dependency blocks downstream workers after its bounded
  attempts are exhausted. The final reviewer still runs and can finish the
  missing work or request targeted repairs. Routing changes and retries are
  recorded in Markdown; budgets cap their number.
- Patches are checked before application. Conflicts are explicitly reported to
  the reviewer, which can inspect the sidekick workspaces and resolve them. A
  conflict does not silently overwrite another sidekick's work.
- Completion reflects the reviewer's structured report plus successful artifact
  capture. It is not an independent proof of correctness; inspect its concrete
  test evidence. `incomplete` and process failures remain visible.

## Configuration and other models

```sh
python3 router.py catalog
python3 router.py --config /absolute/path/custom.json submit ...
```

The shipped defaults are GPT-6 Luna for focused work, Sol for implementation,
and Astra for frontier planning/review. Terra is not a GPT-6 alias in this
configuration; add the exact model ID you can access if you want to use it.
Every assignment and discovery request must specify `model` and `effort`. Python
validates those choices against the catalog and honors them exactly. Assessment
scores, `routing`, discovery-route suggestions, and measured `task_type_routes`
are advice for the orchestrator; they never fill in or override a choice. Legacy
`complexity` and `routing_policy.enforce_frontier_floor` fields do not select routes.
Older plans that omitted model or effort must be updated. The configured retry
ladder remains responsible for escalation after an unsuccessful attempt.

To add a model supported by Codex, add a `models` entry with `provider`, the
exact `model` ID, permitted `efforts`, and optionally `frontier: true`. No Python
changes are needed. Reviewer selection must point to a frontier-marked entry.
Different Codex-compatible endpoints can use another `codex` provider entry
with explicit dotted CLI `config` overrides, for example `model_provider` and
`model_providers.example.base_url`. User config is deliberately not loaded;
configure endpoint/auth environment settings explicitly. Never put secrets in
the JSON configuration: it is snapshotted into Markdown.

For a different agent harness, add a `command` provider:

```json
{
  "type": "command",
  "command": ["/absolute/path/to/adapter", "--some-option"]
}
```

The adapter gets one JSON object on stdin:

```json
{
  "model": "provider-model-id",
  "effort": "medium",
  "prompt": "selected context and assignment",
  "workspace": "/absolute/worktree/path",
  "sandbox": "workspace-write",
  "schema": {"type": "object"}
}
```

It must create its own independent session, supply its own tools, respect the
requested sandbox, and print exactly one JSON envelope to stdout:

```json
{
  "thread_id": "provider-session-id",
  "usage": {"input_tokens": 100, "output_tokens": 30},
  "report": {
    "status": "completed",
    "summary": "What changed",
    "tests": ["Exact command and result"],
    "risks": [],
    "handoff": "What the reviewer needs",
    "checkpoint": null,
    "repair_requests": []
  }
}
```

For a planner invocation, `report` instead conforms to the supplied plan schema.
Provider command arrays are executed without a shell. Configuration is trusted
local code configuration; the command adapter contract does not itself sandbox
an arbitrary provider. A fundamentally different provider protocol needs a
small adapter executable, rather than pretending every API is interchangeable.

Concurrency, maximum sidekick count, per-session timeout, and context/report
size limits are configurable. Effort is validated against each model's allowed
list; unsupported selections fail explicitly. The runner reports actual CLI
token counters, including cached input when available, but does not claim a
hard token budget or infer dollar costs from subscription usage. The observed
token threshold prevents further launches after reported usage crosses it;
in-flight calls and unreported usage can exceed that threshold.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests launch a fake Codex executable through the same process boundary and use
real temporary Git repositories, worktrees, patches, and Markdown stores. They cover
independent threads, context isolation, routing, dependency integration,
conflicts, failures, timeouts, duplicate claims, background execution, and
provider adapters without spending model tokens. Adaptive tests additionally
exercise checkpoints, escalation, thread continuation, executable checks,
repair rounds, stale context, budgets, and measured routing advice. Storage tests
cover competing processes, concurrent readers, atomic writes, interrupted claims,
source-file snapshots, and stable prompt prefixes. No live model calls are made.

Codex's [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
documents JSON events, structured outputs, explicit sandboxing, and saved login
reuse. Model defaults follow [GPT-6 guidance](https://developers.openai.com/api/docs/guides/latest-model).
