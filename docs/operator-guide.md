# Operator Guide

This guide covers the reusable core runtime shipped in `plan-orchestrator` and the additive supervisory control plane that now wraps it.

## 1. Runtime invariants

The public v1 engine still preserves these invariants:

- one worktree per item attempt
- direct markdown playbook ingestion, then normalization
- `codex exec` as the mutation lane
- verification before audit
- frozen audit packet plus candidate patch
- dual audit (`codex exec` and `claude -p`)
- deterministic merged findings before triage
- bounded fix and remediation loops
- explicit `awaiting_human_gate`, `blocked_external`, and `escalated` terminals
- conservative auto-advance only after clean pass conditions

The supervisory layer wraps this kernel; it does not replace it.

## 2. Two control planes now exist

### Runtime-policy control plane

The existing runtime-policy control plane still tunes:

- models
- effort levels
- timeouts
- fix/remediation budgets
- auto-advance defaults

It still resolves from:

1. code defaults
2. repo `plan_orchestrator.json`
3. `run --config <path>`
4. compatibility env vars
5. explicit CLI flags

It still snapshots the resolved payload to:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/runtime_policy.json
```

This plane is for **kernel behavior tuning and provenance** only.

### Supervisory control plane

The new supervisory plane is separate. It adds:

- live attachment proof
- fresh heartbeats
- waiting/terminal observation records
- bounded automatic diagnose/fix/resume behavior
- a separate status view

It writes only under:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/
```

It does **not** change `run_state.json`.

## 3. Which command to use

### Use kernel commands when you want kernel truth

```bash
python automation/run_plan_orchestrator.py run ...
python automation/run_plan_orchestrator.py resume ...
python automation/run_plan_orchestrator.py status ...
python automation/run_plan_orchestrator.py doctor ...
python automation/run_plan_orchestrator.py mark-manual-gate ...
python automation/run_plan_orchestrator.py refresh-run ...
```

### Use supervision commands when live/operator truth matters

```bash
python automation/run_plan_orchestrator.py supervise run ...
python automation/run_plan_orchestrator.py supervise resume ...
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID>
```

`status` stays snapshot-only.

`supervise status` is the live supervisory status view.

## 4. Preflight expectations

Before a fresh run or resume, the runtime still expects:

1. a clean tracked checkout,
2. `git`, `codex`, `claude`, and `python` available,
3. Git identity configured for orchestrator-owned checkpoint commits,
4. no unreviewed ambient agent config unless explicitly acknowledged.

Examples of ambient config paths the runtime checks:

- `~/.codex/config.toml`
- `<repo>/.codex/config.toml`
- `~/.claude/settings.json`
- `<repo>/.claude/settings.json`
- `<repo>/.mcp.json`

If those are present, the runtime still requires:

```bash
export PLAN_ORCHESTRATOR_CLEAN_ENV_CONFIRMED=1
```

Use that override only when you have intentionally reviewed the environment.

To surface those checks without starting a run, use:

```bash
python automation/run_plan_orchestrator.py doctor \
  --playbook path/to/playbook.md \
  --format text
```

## 5. Snapshot commands

### Inspect one saved run

```bash
python automation/run_plan_orchestrator.py status \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format json
```

### List all saved runs

```bash
python automation/run_plan_orchestrator.py status \
  --all \
  --format text
```

### Diagnose and validate

```bash
python automation/run_plan_orchestrator.py doctor \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format json
```

### Safe deterministic repair only

```bash
python automation/run_plan_orchestrator.py doctor \
  --run-id RUN_20260325T120000Z_deadbeef \
  --fix-safe \
  --format json
```

`doctor --fix-safe` still only rebuilds deterministic local orchestrator artifacts such as `normalized_plan.json`. It does not rewrite tracked repo files, rerun model stages, or recreate historical provenance artifacts such as `runtime_policy.json`.

## 6. Supervision commands

### Start a new supervised run

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook path/to/playbook.md \
  --item 01
```

You can also use:

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook path/to/playbook.md \
  --next
```

Optional supervision-specific flags:

```bash
--evidence-inbox-dir /absolute/path/to/inbox
--heartbeat-interval-sec 15
--probe-ack-deadline-sec 5
--stale-after-sec 45
--waiting-poll-interval-sec 60
--waiting-stale-timeout-sec 180
--max-auto-resume-attempts 2
--max-wait-seconds 600
```

### Re-enter a saved run under supervision

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id RUN_20260325T120000Z_deadbeef
```

If a blocked item already has local evidence ready:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id RUN_20260325T120000Z_deadbeef \
  --external-evidence-dir /absolute/path/to/evidence
```

If you want the supervisor to watch a local inbox and resume automatically when evidence appears later:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id RUN_20260325T120000Z_deadbeef \
  --evidence-inbox-dir /absolute/path/to/inbox
```

### Inspect supervisory status

```bash
python automation/run_plan_orchestrator.py supervise status \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format text
```

Machine-readable output:

```bash
python automation/run_plan_orchestrator.py supervise status \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format json \
  --exit-code
```

`supervise status --exit-code` returns the supervisory plane code, not the kernel `status` code.

## 7. Stable `supervise status --exit-code` contract

| supervisory outcome | exit code | meaning |
|---|---:|---|
| `live_attached` | 0 | Fresh probe/ack evidence proves live attachment to the current kernel invocation. |
| `waiting_state_observed` | 10 | Supervisor recently observed a waiting state such as pending manual gate or blocked external evidence. |
| `attachment_unproven` | 11 | A live run may still exist, but fresh attachment proof is missing or stale. |
| `terminal_observed` | 12 | Supervisor observed a terminal completion or a parked non-recoverable case. |
| `snapshot_only` | 13 | No fresh supervisory evidence is available; only snapshot/kernel artifacts are available. |

## 8. Manual-gate workflow

The human boundary is unchanged.

When an item ends in `awaiting_human_gate`:

1. inspect the current run,
2. review the current attempt bundle,
3. record approval or rejection with `mark-manual-gate`,
4. let the supervisor observe that recorded decision and continue only when resume semantics remain truthful.

The supervisor never writes the decision itself.

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id RUN_20260325T120000Z_deadbeef \
  --item 01 \
  --decision approved \
  --by "Reviewer Name" \
  --note "Required review completed." \
  --evidence-path docs/reviews/signoff.md
```

## 9. External evidence workflow

The local-file boundary is unchanged.

When an item is `blocked_external`, the runtime still expects a local directory:

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id RUN_20260325T120000Z_deadbeef \
  --external-evidence-dir /absolute/path/to/evidence
```

The supervisor can automate that same path, but it still only uses local files.

It does not browse the web or fabricate evidence.

## 10. Resume semantics

`resume` still trusts `run_state.json`.

It still:

- refuses `awaiting_human_gate` until a human decision is recorded,
- requires evidence for `blocked_external`,
- resets blocked/external and escalated items to a fresh-attempt boundary,
- creates a new worktree attempt instead of rewriting history.

The supervisor reuses those same semantics. It does not invent a second resume path.

## 11. Supervision artifact layout

Each supervised run now has:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/
```

Contents:

```text
bridge_registration.json
active_stage.json
probe_request.json
probe_ack.json
control.lock
heartbeats/
interventions/
invocations/
```

Artifact classes:

- `bridge_registration.json` — live bridge discovery for the current kernel invocation
- `active_stage.json` — schema-validated current blocking child-stage metadata
- `probe_request.json` / `probe_ack.json` — ephemeral nonce challenge/ack files
- `heartbeats/*.json` — durable per-heartbeat evidence files
- `interventions/*.json` — durable diagnosis/repair/resume/park action records
- `invocations/*.stdout.log` / `*.stderr.log` — kernel child logs referenced by interventions

## 12. Inspection order

When live/operator truth matters, inspect in this order:

1. `supervise status --run-id ...`
2. `status --run-id ...`
3. `doctor --run-id ...`
4. `run_state.json`
5. current item `latest_paths`
6. `manual_gate.json` or `escalation_manifest.json`
7. model reports under `.local/ai/plan_orchestrator/runs/<RUN_ID>/`

When only kernel snapshot truth matters, skip step 1 and start with `status`.

## 13. Important scope note

The supervisor preserves current kernel resume semantics.

That means:

- automatic recovery safely resumes the same blocked/external/escalated frontier;
- manual-gate continuation is automatically resumed only when doing so remains truthful for the current resume semantics;
- explicit historical `run --items ...` queue intent is **not** reconstructed into a new kernel state machine.

If you want run-level continuation after a saved stop, `supervise resume` is the correct operator entry point.
