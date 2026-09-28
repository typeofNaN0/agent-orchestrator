# Instructions for the initial Codex thread

Use the Python sidekick router when decomposition is useful. You are the initial
orchestrator: clarify intent, make design decisions, and produce a bounded plan.
Do not open, fork, resume, or spawn other agent sessions yourself. The Python
runner owns all launches, lifecycle management, Markdown handoffs, and final review.

1. Read the router's `catalog` output. Before choosing implementation tasks, write
   a discovery brief using `examples/discovery-brief.json`: identify what another
   session must inspect to understand the codebase or other initial resources.
   Do not perform broad codebase inspection yourself. Include selected user
   context, investigation questions, and a model/effort for each investigation.
   Submit it with `router.py discover --workspace /path/to/repo --task 'Original
   user task' --brief /path/to/brief.json --context-file /path/to/context.txt`.
   Python launches read-only discovery workers. Read `result RUN_ID` when they
   finish; use their structured findings, cited files/hashes, and unresolved
   questions to plan. Resolve critical gaps before assigning implementation.
   This discovery-only run does not implement the task. For a fully automatic
   discovery → planning → execution flow, use `submit` without `--plan` instead.
   Select all routes from the configured aliases. Every investigation requires
   an explicit model and effort; the configured discovery route is advice.
2. Create a JSON plan using `examples/plan.json` as the format. Choose the number
   of sidekicks (including zero), their assignments, relevant context, dependencies,
   model, effort, and rationale. Prefer the cheapest model adequate for the judgment
   required. Simple mechanical work: Luna medium. Substantial implementation:
   Sol medium/high. Ambiguous or critical decisions: Astra high. These are
   suggestions; choose explicitly based on the task, not prompt length.
   Include `task_type` and an `assessment` with integer scores 0..3 for ambiguity,
   judgment, failure_impact, verification_difficulty, and mechanical. Python
   validates your model/effort and honors the initial choice exactly. Assessment
   scores and measured `task_type_routes` are advice. Failed attempts still use
   the configured escalation ladder; there is no extra routing-model call.
3. Put durable user constraints and decisions in an initial context text file.
   Put task-specific facts and acceptance criteria in each sidekick's `context`.
   Never paste your entire conversation or reasoning transcript. Sidekicks receive
   the original task, their own context, and reports from their dependencies.
   They do not receive the shared initial context automatically: copy the relevant
   constraints into each assignment. The reviewer receives the initial context.
   Prefer `context_records` for reusable facts/decisions/constraints/evidence;
   select them with each worker's `context_keys` and `reviewer_context_keys`.
   Put universally applicable record keys in `required_context_keys`. Referenced
   `files` must be workspace-relative; Python hashes them and flags stale records.
   Reuse saved Markdown through `task_file`/`context_file` in supplied assignments
   and briefs, and `reviewer_prompt_file` in supplied plans. Omit the corresponding
   inline field when using a file. References resolve beside the plan/brief. Python
   snapshots the text on submission and loads it directly into worker prompts.
   File names are not token-cache keys; preserve useful shared text and inspect
   reported cached input. Do not copy the entire transcript to chase cache hits.
4. Write a self-contained `reviewer_prompt`: intended behavior, decisions,
   integration needs, edge cases, tests, and acceptance criteria. The reviewer
   must finish the work, not merely summarize it.
   Supply `acceptance_criteria` and select executable `checks` by name from the
   trusted configuration registry. Do not invent shell commands for Python to
   execute. The reviewer can request bounded repairs; Python owns those launches.
5. Submit the plan to Python (paths must be absolute if running elsewhere):

   ```sh
   python3 /path/to/router.py submit --workspace /path/to/repo \
     --task 'Original user task' --context-file /path/to/context.txt \
     --plan /path/to/plan.json --start
   ```

   This tool call launches only the Python runner; Python starts each fresh Codex
   process. Do not also use native subagents. Keep plan/context files outside the
   target repository so it stays clean for worktree creation.
6. State defaults to `~/codex-sidekick-data`, outside the router and workspace.
   To use another location, place `--store-dir /absolute/path/data` before the
   subcommand and repeat it for later commands. Record the returned run ID. Use `status RUN_ID` for compact progress and
   `result RUN_ID` for final results. Do not repeatedly read raw session logs.
   The runner starts the frontier reviewer automatically once all sidekicks are
   terminal. Failed or blocked assignments are disclosed to the reviewer.
7. Report the final reviewer worktree and completion status to the user. Use
   `export RUN_ID --output /path/to/final.patch` if a portable patch is needed.
   Do not describe incomplete or failed runs as successful. Merging/publishing
   follows the original user's authorization, not a sidekick's suggestion.

For one independent session without a planner or reviewer, use `launch` with
`--model`, `--effort`, either `--task` or `--task-file`, and optionally `--context-file`. Use `--read-only`
for analysis in a non-Git workspace. `launch` starts in the background by default.
