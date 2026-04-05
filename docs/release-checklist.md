# Release Checklist

This checklist is for operators running `plan-orchestrator` during a real rollout.

## 1. Prepare the checkout

- Start from a clean tracked checkout.
- Confirm `git`, `python`, `codex`, and `claude` are available.
- Confirm Git identity is configured.
- If the machine has local agent config you intentionally want to allow, review it first and then set:

```bash
export PLAN_ORCHESTRATOR_CLEAN_ENV_CONFIRMED=1
```

## 2. Decide the runtime policy

- If the release needs non-default model choices, time limits, retry budgets, or auto-advance defaults, set them in `plan_orchestrator.json`.
- If the run needs a one-off override, prepare a JSON overlay and pass it with `run --config ...` or `supervise run --config ...`.
- Treat the resolved runtime policy snapshot as part of the release record.

## 3. Run diagnostics before starting

Run a preflight check against the playbook:

```bash
python automation/run_plan_orchestrator.py doctor \
  --playbook path/to/playbook.md \
  --format text
```

Do not start the release run until this check is clean or you understand every reported issue.

## 4. Start the release run under supervision

Use the supervision surface for a real monitored operator run.

One item:

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook path/to/playbook.md \
  --item 01
```

First unfinished item:

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook path/to/playbook.md \
  --next
```

One-off runtime-policy overlay:

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook path/to/playbook.md \
  --item 01 \
  --config ops/runtime-policy.json
```

Optional blocked-external inbox watch:

```bash
python automation/run_plan_orchestrator.py supervise run \
  --playbook path/to/playbook.md \
  --item 03 \
  --evidence-inbox-dir /absolute/path/to/inbox
```

## 5. Monitor the run truthfully

Use `supervise status` when live/operator truth matters:

```bash
python automation/run_plan_orchestrator.py supervise status \
  --run-id <RUN_ID> \
  --format text
```

Use machine-readable supervisory exit codes when automation needs them:

```bash
python automation/run_plan_orchestrator.py supervise status \
  --run-id <RUN_ID> \
  --format json \
  --exit-code
```

Stable supervisory exit codes:

- `0` — `live_attached`
- `10` — `waiting_state_observed`
- `11` — `attachment_unproven`
- `12` — `terminal_observed`
- `13` — `snapshot_only`

Use kernel `status` for authoritative saved kernel state:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format json
```

## 6. Handle stop points correctly

### Manual gate

Humans still own approval/rejection.

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id <RUN_ID> \
  --item <ITEM_ID> \
  --decision approved \
  --by "Reviewer Name" \
  --note "Required review completed."
```

### Blocked external evidence

Use only local evidence files:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id <RUN_ID> \
  --external-evidence-dir /absolute/path/to/evidence
```

Or keep the supervisor watching an inbox:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id <RUN_ID> \
  --evidence-inbox-dir /absolute/path/to/inbox
```

### Escalated

Inspect first. The supervisor may repair or retry bounded recoverable cases, but it will park non-recoverable cases truthfully rather than inventing progress.

## 7. Repair only the orchestrator's local bookkeeping

If deterministic run-local artifacts drift, safe repair is still:

```bash
python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --fix-safe \
  --format json
```

The supervisor reuses this exact boundary. It does not widen it.

## 8. Confirm the final release record

Before merging or handing off:

- inspect `supervise status --run-id ...`
- inspect `status --run-id ...`
- confirm the expected kernel terminal state
- confirm the supervision outcome is truthful
- confirm the latest artifact paths exist
- confirm any runtime-policy warning is understood
- keep the run directory for auditability

Useful files:

- `.local/automation/plan_orchestrator/runs/<RUN_ID>/run_state.json`
- `.local/automation/plan_orchestrator/runs/<RUN_ID>/runtime_policy.json`
- `.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/`
- `.local/automation/plan_orchestrator/runs/<RUN_ID>/items/<ITEM_ID>/attempt-<N>/`
- `.local/ai/plan_orchestrator/runs/<RUN_ID>/`

## 9. If something looks wrong

Start with:

```bash
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

Then follow `docs/troubleshooting.md`.
