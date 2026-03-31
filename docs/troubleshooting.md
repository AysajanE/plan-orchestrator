# Troubleshooting

Use this guide when a run does not behave the way you expect.

## Start here

Check the run before opening raw files:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format json

python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --format json
```

If you are checking the environment before a run exists:

```bash
python automation/run_plan_orchestrator.py doctor \
  --playbook path/to/playbook.md \
  --format json
```

## Symptom: doctor fails before a run starts

Common causes:

- the checkout is not clean
- required commands are missing
- Git identity is not configured
- ambient agent config was detected

What to do:

1. Read the failing check in `doctor`.
2. Fix the environment issue directly.
3. Only set `PLAN_ORCHESTRATOR_CLEAN_ENV_CONFIRMED=1` if you intentionally reviewed the local agent config.

## Symptom: status says `waiting`

This means the orchestrator stopped on purpose.

Possible causes:

- `awaiting_human_gate`
- `blocked_external`

What to do for a manual gate:

1. Inspect the run with `status --run-id ... --format json`.
2. Review the manual-gate record under the current attempt.
3. Record the human decision with `mark-manual-gate`.

What to do for blocked external evidence:

1. Gather the required outside evidence.
2. Resume with `resume --external-evidence-dir ...`.

## Symptom: status says `warning`

This usually means the run is still usable, but a provenance artifact needs attention.

Current warning class:

- the saved `runtime_policy.json` snapshot is missing
- the saved `runtime_policy.json` no longer matches the run state

What to do:

1. Treat this as an auditability issue, not a reason to rewrite history.
2. Use `status --run-id ... --format json` to see the runtime-policy checks.
3. Use `doctor --run-id ... --format json` to confirm the same warning.
4. Do not hand-edit or recreate `runtime_policy.json` unless you are explicitly doing a forensic/manual repair outside normal workflow.

## Symptom: status says `error`

This means the run is not in a healthy operating state.

Common causes:

- escalated item
- missing run-local artifacts
- missing referenced worktree
- missing branch or checkpoint ref

What to do:

1. Inspect `status` and `doctor` JSON output.
2. If the issue is local deterministic orchestrator state, try:

```bash
python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --fix-safe \
  --format json
```

3. If the item is escalated, inspect the escalation manifest before deciding what to do next.

## Symptom: `doctor --fix-safe` did not fix everything

That is expected in some cases.

`doctor --fix-safe` is intentionally narrow:

- it may rebuild `normalized_plan.json`
- it may report stale refs or orphaned worktrees
- it does not rerun AI stages
- it does not recreate historical provenance artifacts
- it does not modify tracked repo content

If the remaining problem is not deterministic local orchestrator state, operator intervention is required.

## Symptom: resume does not continue the way you expected

Resume trusts `run_state.json`.

What to check:

1. Is the current item at `awaiting_human_gate`?
2. Is the current item `blocked_external` without `--external-evidence-dir`?
3. Is the run already fully passed?

Useful commands:

```bash
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

## Symptom: auto-advance behavior is surprising

Auto-advance can now be enabled in runtime policy, not only by passing `--auto-advance` on the command line.

What to check:

1. inspect the run's `runtime_policy.json`
2. inspect `run_state.json`
3. inspect `status --run-id ... --format json`

The active run may be using:

- repo `plan_orchestrator.json`
- `run --config ...`
- compatibility env vars
- `--auto-advance`

## Inspection order

When in doubt, inspect in this order:

1. current working tree cleanliness
2. playbook path
3. `status --run-id ...`
4. `doctor --run-id ...`
5. `run_state.json`
6. the current item's `latest_paths`
7. `manual_gate.json` or `escalation_manifest.json` when present
8. model reports under `.local/ai/plan_orchestrator/runs/<RUN_ID>/`
