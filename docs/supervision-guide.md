# Supervision Guide

This guide covers the supervisory control plane added around the existing `plan-orchestrator` kernel.

## 1. Purpose

The supervisory plane exists to do four things truthfully:

1. continuously monitor a real run end to end,
2. prove fresh live attachment when that proof exists,
3. diagnose stoppages from current saved state and supervisory evidence,
4. fix recoverable issues and resume automatically where appropriate.

It does **not** replace the kernel.

## 2. Preserved invariants

The following remain unchanged:

- `run_state.json` is still the sole authoritative kernel-state file
- `status` is still snapshot-only
- `doctor` is still deterministic safe repair only
- runtime policy remains a separate provenance/tuning plane
- worktree-per-attempt isolation remains unchanged
- verification still precedes audit
- `awaiting_human_gate` remains the only human-only stop
- blocked external evidence still uses only local files

## 3. Commands

### Start a supervised run

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook path/to/playbook.md \
  --item 01
```

### Resume a saved run under supervision

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id RUN_20260325T120000Z_deadbeef
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

## 4. Stable `supervise status --exit-code` contract

| claim_class | exit_code | meaning |
|---|---:|---|
| `live_attached` | 0 | Fresh probe/ack evidence matched the current bridge, `run_state.json`, and `active_stage.json` when present. |
| `waiting_state_observed` | 10 | Supervisor recently observed a truthful waiting state such as pending manual gate or missing external evidence. |
| `attachment_unproven` | 11 | A live child may still exist, but fresh attachment proof is missing or stale. |
| `terminal_observed` | 12 | Supervisor observed a terminal completion or a parked non-recoverable case. |
| `snapshot_only` | 13 | No fresh supervisory evidence exists; only saved kernel artifacts are available. |

Important: these are **supervisory** exit codes, not kernel `status` exit codes.

## 5. Freshness policy defaults

Chosen defaults:

- live probe interval: `15` seconds
- probe acknowledgement deadline: `5` seconds
- live stale timeout: `45` seconds
- waiting poll interval: `60` seconds
- waiting stale timeout: `180` seconds
- recoverable escalated retry budget: `2` attempts per escalation fingerprint

You can override these on `supervise run` and `supervise resume`.

## 6. Artifact layout

Every supervised run writes under:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/
```

### Ephemeral coordination files

```text
bridge_registration.json
active_stage.json
probe_request.json
probe_ack.json
control.lock
```

### Durable evidence

```text
heartbeats/<SEQ>_<TIMESTAMP>.json
interventions/<SEQ>_<ACTION>.json
invocations/<KERNEL_INVOCATION_ID>.stdout.log
invocations/<KERNEL_INVOCATION_ID>.stderr.log
```

There is intentionally **no** `supervision_state.json`.

`run_state.json` remains the kernel authority.

## 7. Live attachment proof

A `live_attached` claim requires a fresh nonce challenge/ack cycle:

1. supervisor writes `probe_request.json`
2. live bridge writes `probe_ack.json`
3. supervisor independently re-hashes `run_state.json`
4. if `active_stage.json` exists, supervisor independently re-hashes it too
5. only then may it write a `live_attached` heartbeat

A stale PID, stale old ack, old heartbeat, unchanged artifact set, or unchanged `run_state.updated_at_utc` is **not** enough.

## 8. Fail-closed rule

If a fresh live probe cannot be satisfied, the supervisor must stop claiming `live_attached`.

It may continue polling and writing:

- `attachment_unproven` heartbeats for a possibly-live-but-unprovable kernel,
- `waiting_state_observed` heartbeats for truthful waiting states,
- `terminal_observed` heartbeats for terminal observation,
- `snapshot_only` heartbeats for snapshot-driven diagnosis or action planning.

It must not keep extending an old `live_attached` claim.

## 9. Active-stage contract

`supervision/active_stage.json` is schema-validated on write and read.

It reports the current blocking child stage when one exists, including:

- stage name
- item id
- attempt number
- child tool
- child pid
- start time

The supervisor uses it only as live operational metadata. It is not added to model lane packets and does not change kernel state.

## 10. Intervention behavior

The supervisor reuses existing repo surfaces in this order:

1. `supervise status` for live truth
2. `status` for kernel snapshot truth
3. `doctor` for deterministic repair
4. `resume` for truthful fresh-attempt continuation
5. `mark-manual-gate` remains human-only

Automatic intervention stays inside these boundaries.

### Allowed automatic actions

- observe and wait
- run `doctor --fix-safe`
- resume a blocked item once valid local evidence exists
- resume a recoverable escalated item within budget
- resume after an already-recorded human approval when current resume semantics remain truthful
- park a non-recoverable case with an intervention record

### Disallowed automatic actions

- approving or rejecting a manual gate
- browsing the web for evidence
- fabricating evidence
- recreating historical provenance artifacts
- widening scope beyond truthful kernel resume semantics
- inventing a second kernel state machine

## 11. Manual-gate boundary

`awaiting_human_gate` remains the only human-only stop.

Humans still own:

- approval or rejection
- the `mark-manual-gate` write action
- any off-workflow forensic/manual repair outside normal runtime behavior

The supervisor may only resume after an already-recorded human decision, and only when using existing resume semantics remains truthful.

## 12. Blocked external boundary

`blocked_external` still uses local evidence files only.

The supervisor can:

- watch `--evidence-inbox-dir`
- detect when a local evidence directory becomes available
- invoke the existing `resume --external-evidence-dir ...` path

It cannot browse or invent evidence.

## 13. Explicit queue scope note

The kernel persists item state, not an explicit historical `run --items ...` queue.

Because the package preserves the kernel rather than replacing it, automatic post-stop continuation stays limited to cases where current kernel resume semantics remain truthful:

- same blocked item,
- same escalated item,
- or run-level continuation through `supervise resume` / resolved auto-advance.

If you want full run-level continuation after a saved stop, `supervise resume` is the intended operator entry point.

## 14. Quick operator checks

Check supervisory truth:

```bash
python automation/run_plan_orchestrator.py supervise status \
  --run-id <RUN_ID> \
  --format json \
  --exit-code
```

Check kernel snapshot truth:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format json

python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --format json
```
