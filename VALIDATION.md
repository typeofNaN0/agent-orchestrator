# Validation

## Version 0.4

- 82 automated tests pass with `python3 -B -m unittest discover -s tests -v`.
- Router state uses real temporary Markdown stores. The suite exercises parallel
  processes on independent runs, competing claims for the same run, cross-process
  write rejection, thread-safe attempt budgets, concurrent readers, atomic write
  failures, interrupted claims, malformed records, and hidden staging directories.
- Prompt coverage includes CLI and plan/brief paths, UTF-8 and size validation,
  ambiguous inputs, source hashes, snapshots preserved through delayed background
  execution, exact prompt capture, stable prefixes across different source paths,
  and worker/reviewer context isolation.
- Explicit initial model/effort choices are preserved even with high judgment
  scores or the legacy frontier flag; omitted choices fail. Existing retry,
  discovery, verification, repair, metrics, and patch integration tests still pass.
- Tests use simulated model processes and real Git repositories/worktrees. No
  live model calls or live cache-savings measurements were performed. The cached
  usage tests verify reported counters, not actual cache hits.
- Existing SQLite history is not imported or modified. Historical validation
  entries below describe the earlier versions, not the current storage format.

## Version 0.3

- 62 automated tests pass, including 13 discovery-specific tests.
- The tests verify briefing → read-only discovery → informed planning ordering,
  findings/evidence persistence, model selection after discovery, bounded follow-up
  investigations, propagation of failed investigations, rejection of fabricated
  file citations and write violations, optional skipping, explicit-plan bypass,
  discovery-only runs with externally supplied briefs, route/count validation,
  and checkpoint-driven escalation that preserves the read-only sandbox.
- The complete suite uses simulated model processes with real SQLite databases,
  subprocesses, Git repositories/worktrees, file hashing, and patch integration.
- The `discover` CLI and example brief were validated locally. No additional
  live model calls were needed for this change; a live discovery-to-planning
  multi-model run has not been performed.

## Version 0.2

The adaptive implementation adds checkpoint monitoring, bounded escalation,
independent executable verification, reviewer repair rounds, shared context
freshness, metrics/tuning, and optional same-worker thread continuation.

- 49 automated tests cover the original runner and adaptive behavior, using
  simulated model processes with real SQLite, Git worktrees, command execution,
  timeout/termination behavior, and patch integration.
- The suite covers live checkpoint-triggered interruption with partial-edit
  preservation, repeated failures, stalls, same-route infrastructure retries,
  evidence-driven escalation, verification overriding false success, targeted
  reviewer repairs, stale/selective context, atomic concurrent attempt limits,
  observed-token limits, malformed reports, and verified-sample tuning.
- Optional resume command arguments were accepted by the installed CLI parser.
  Same-thread identity and reviewer isolation are covered by simulated sessions.
- A live read-only Luna medium smoke test passed with the new report schema and
  attempt storage: run `3fddfba14f3246e4827f49f291d02aa4`, thread
  `01a0e15a-b8c9-7c53-828d-485c0b64288b`.
- That live run reported 14,537 input tokens and 67 output tokens (22 reasoning
  output tokens reported separately), with no cached input tokens.

Live model escalation, cache savings from actual resumed threads, and a live
multi-model repair pipeline have not been benchmarked. Automated tests exercise
those control paths through simulated provider processes. No cost-reduction or
quality-parity claim is made.

## Version 0.1 baseline

Validated on September 27, 2026, with Python 3.9.6 and Codex CLI 0.157.1.

- 19 standard-library integration/unit tests passed. Model calls were simulated;
  subprocesses, SQLite, Git repositories, worktrees, and patch application were real.
- A live, read-only `gpt-6-luna` session with `medium` effort completed using the
  existing Codex ChatGPT login. It used the actual JSON-schema and CLI invocation
  implemented by the router, and persisted its report/thread ID/usage in SQLite.
- Live run ID: `582242d12daa4817934cb911a8eb2956`.
- Live Codex thread ID: `01a0e142-e412-7b32-b779-9307973d7179`.
- Reported usage: 13,971 input tokens, 35 output tokens, zero cached input tokens.
  This demonstrates that even a tiny fresh session has harness/context overhead;
  it is not a cost-savings benchmark.
- The first smoke-test attempt was blocked by the outer execution sandbox before
  Codex initialized. The successful attempt ran with permission outside that outer
  sandbox; the launched Codex session retained its own `read-only` sandbox.

The original complete pipeline was tested with simulated model processes. A live
multi-model code-editing pipeline, other provider endpoints, automatic crash
recovery, and comparative cost/quality benchmarks have not been validated.
