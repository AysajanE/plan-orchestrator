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

- If this release needs non-default model choices, time limits, retry budgets, or auto-advance defaults, set them in `plan_orchestrator.json`.
- If this run needs a one-off override, prepare a JSON overlay and pass it with `run --config ...`.
- Treat the resolved runtime policy as part of the release record. Every run snapshots it.

## 3. Run diagnostics before starting

Run a preflight check against the playbook:

```bash
python automation/run_plan_orchestrator.py doctor \
  --playbook path/to/playbook.md \
  --format text
```

Do not start the release run until this check is clean or you understand every reported issue.

## 4. Start the release run

Choose the smallest correct scope:

- One item:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --item 01
```

- First unfinished item:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --next
```

- One-off runtime-policy overlay:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --item 01 \
  --config ops/runtime-policy.json
```

## 5. Monitor the run

Use `status` rather than reading raw files first:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format text
```

Use `--format json --exit-code` when this is being checked by automation:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format json \
  --exit-code
```

Expected meanings:

- `ok`: the run is healthy
- `warning`: the run is usable, but part of the provenance trail needs attention
- `waiting`: the run is waiting for a person or outside evidence
- `error`: the run or its local state needs intervention

## 6. Handle stop points correctly

When `status` shows a manual gate:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id <RUN_ID> \
  --item <ITEM_ID> \
  --decision approved \
  --by "Reviewer Name" \
  --note "Required review completed."
```

When the run is blocked on outside evidence:

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id <RUN_ID> \
  --external-evidence-dir /absolute/path/to/evidence
```

When the run is escalated, inspect first before choosing the next action:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format json
```

## 7. Repair only the orchestrator's local bookkeeping

If the run's local artifacts drift or a deterministic file is missing, use safe repair:

```bash
python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --fix-safe \
  --format json
```

`doctor --fix-safe` only rebuilds deterministic local orchestrator artifacts. It does not rewrite tracked repo files, rerun model stages, or recreate historical provenance artifacts such as `runtime_policy.json`.

## 8. Confirm the final release record

Before merging or handing off:

- check `status --run-id ...`
- confirm the expected terminal state
- confirm the latest artifact paths exist
- confirm any runtime-policy warning is understood
- keep the run directory for auditability

Useful files:

- `.local/automation/plan_orchestrator/runs/<RUN_ID>/run_state.json`
- `.local/automation/plan_orchestrator/runs/<RUN_ID>/runtime_policy.json`
- `.local/automation/plan_orchestrator/runs/<RUN_ID>/items/<ITEM_ID>/attempt-<N>/`
- `.local/ai/plan_orchestrator/runs/<RUN_ID>/`

## 9. If something looks wrong

Start with:

```bash
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

Then follow `docs/troubleshooting.md`.
