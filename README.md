# plan-orchestrator

Run approved AI repo changes one item at a time in isolated git worktrees, with verification, dual audits, explicit human/external stop points, and an additive supervisory control plane for truthful live monitoring and bounded automatic recovery.

Plan Orchestrator stays intentionally boring:

- one reviewed `markdown_playbook_v1` file as the public input contract
- one orchestrator-owned worktree per item attempt
- verification before either audit lane
- Codex + Claude auditing the same frozen packet
- deterministic findings merge before triage
- explicit `passed`, `awaiting_human_gate`, `blocked_external`, and `escalated` terminals
- local/offline-first defaults and no agent-owned git operations

The new supervisory layer wraps that kernel; it does **not** replace it.

## What supervision adds

The supervisory control plane adds three new commands:

```bash
python automation/run_plan_orchestrator.py supervise run ...
python automation/run_plan_orchestrator.py supervise resume ...
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID>
```

Those commands add:

- a long-lived parent supervisor around the current kernel
- nonce-based live-attachment probes with fail-closed liveness claims
- schema-validated supervision artifacts under the run root
- bounded diagnose → `doctor --fix-safe` → `resume` automation for recoverable non-manual stops
- truthful waiting observation for manual gates and blocked external evidence
- a separate supervisory status plane that does **not** redefine `status` or `doctor`

## Preserved boundaries

The package preserves these kernel invariants:

- `run_state.json` remains the sole authoritative kernel-state file
- `status` remains a snapshot status view
- `doctor` remains the deterministic safe-repair surface
- runtime policy remains a separate provenance/tuning plane
- worktree-per-attempt isolation remains unchanged
- verification still happens before audit
- `awaiting_human_gate` remains the only human-only stop

## Fastest way to understand the repo

### Tier 1: inspect the approved plan

```bash
python automation/run_plan_orchestrator.py list-items \
  --playbook examples/launch_demo_playbook/playbook.md

python automation/run_plan_orchestrator.py show-item \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 01 \
  --format text
```

### Tier 2: inspect snapshot state

```bash
python automation/run_plan_orchestrator.py status \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format json

python automation/run_plan_orchestrator.py doctor \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format json
```

### Tier 3: run under live supervision

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 01

python automation/run_plan_orchestrator.py supervise status \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format json \
  --exit-code
```

See `docs/supervision-guide.md` for the full supervision contract.

## Kernel commands vs supervision commands

### Kernel commands

These commands keep their existing meanings:

- `run`
- `resume`
- `status`
- `doctor`
- `mark-manual-gate`
- `refresh-run`

Use them when you want direct kernel execution, snapshot inspection, or the authoritative manual-gate write boundary.

### Supervision commands

Use these when you want real operator/live-run truth:

- `supervise run`
- `supervise resume`
- `supervise status`

`supervise status` reports both planes:

- **kernel plane** — from `run_state.json` and `status`
- **supervision plane** — from fresh heartbeats, probe evidence, and current bridge state

## What this is not

- not a planner that invents work
- not a generic chat shell
- not a web-browsing agent
- not a replacement kernel state machine
- not a second authoritative state plane
- not an auto-approver for manual gates

## Manual gate boundary

`awaiting_human_gate` remains the only human-only stop.

Humans still own:

- approving or rejecting the gate
- recording that decision with `mark-manual-gate`
- deciding whether to operate outside the normal workflow when the supervisor parks a non-recoverable case

The supervisor may only continue **after** a human decision is already recorded and resume semantics remain truthful.

## Local artifact layout

Run-control artifacts:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/
```

Model JSON reports:

```text
.local/ai/plan_orchestrator/runs/<RUN_ID>/
```

Per-item worktrees:

```text
.local/automation/plan_orchestrator/worktrees/<RUN_ID>/item-<ITEM_ID>-attempt-<N>/
```

New supervision artifacts:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/
```

Supervision contents:

```text
bridge_registration.json
active_stage.json
probe_request.json
probe_ack.json
control.lock
heartbeats/<SEQ>_<TIMESTAMP>.json
interventions/<SEQ>_<ACTION>.json
invocations/<KERNEL_INVOCATION_ID>.stdout.log
invocations/<KERNEL_INVOCATION_ID>.stderr.log
```

## Snapshot truth vs live truth

Use the right surface for the right question:

- **What does the authoritative saved run say?**  
  Use `status`, `doctor`, and `run_state.json`.

- **Can the operator loop still prove fresh live attachment right now?**  
  Use `supervise status`.

If the supervisor cannot prove fresh attachment, it downgrades to `attachment_unproven` or `snapshot_only`. It does not keep claiming live supervision from stale evidence.

## Full-run prerequisites

The no-credential inspection path only needs Python and the repo checkout.

A full `run`, `resume`, `mark-manual-gate`, `supervise run`, or `supervise resume` walkthrough expects:

- Python 3.10, 3.11, or 3.12
- `git`, `bash`, `codex`, and `claude` available in `PATH`
- Git identity configured for checkpoint commits
- a clean tracked checkout
- no unreviewed ambient agent configuration unless intentionally acknowledged

## Safety defaults

The runtime remains reproducibility-first and local/offline-first:

- no agent-owned git operations
- no implicit web browsing by execution, audit, triage, fix, or remediation
- no destructive reset/clean/rebase/squash automation
- no automatic manual-gate approvals or rejections
- no fabricated external evidence
- only a passed item, or an item later approved through a manual gate, can advance the run branch

## Docs

- `docs/playbook-contract.md` — public input contract
- `docs/operator-guide.md` — kernel and operator surface
- `docs/troubleshooting.md` — snapshot + supervision troubleshooting
- `docs/release-checklist.md` — rollout checklist
- `docs/supervision-guide.md` — live supervision contract, artifacts, and exit codes
- `docs/demo-run.md` — existing kernel demo flow
- `docs/launch-proof.md` — historical proof captures

## Package self-check

After copying the repo into its standalone home, run:

```bash
python -m unittest discover -s automation/plan_orchestrator/tests -t .
```

Capture that command's output as the package verification record for the extracted repo.

For supervisory-lane verification after apply, see `docs/supervision-guide.md#15-post-apply-verification`.
