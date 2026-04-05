# Troubleshooting

Use this guide when a run does not behave the way you expect.

## Start here

If the run was started or resumed under supervision, inspect the supervisory plane first:

```bash
python automation/run_plan_orchestrator.py supervise status \
  --run-id <RUN_ID> \
  --format json
```

Then inspect the kernel snapshot surfaces:

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

## Symptom: `supervise status` says `live_attached`

This is the healthy live case.

It means the supervisor recently proved fresh attachment to the current kernel invocation with a new probe/ack round.

What to do:

- use `supervise status` for live/operator truth
- use `status` if you want the saved kernel snapshot
- inspect `supervision/active_stage.json` only if you need raw active-stage detail

## Symptom: `supervise status` says `waiting_state_observed`

This means the supervisor is still polling, but the kernel is intentionally waiting.

Common causes:

- `awaiting_human_gate`
- `blocked_external`

What to do:

1. inspect `status --run-id ... --format json`
2. inspect `doctor --run-id ... --format json`
3. follow the correct wait path:

For a manual gate:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id <RUN_ID> \
  --item <ITEM_ID> \
  --decision approved \
  --by "Reviewer Name" \
  --note "Required review completed."
```

For blocked external evidence:

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id <RUN_ID> \
  --external-evidence-dir /absolute/path/to/evidence
```

Or let the supervisor watch `--evidence-inbox-dir` and resume when local evidence appears.

## Symptom: `supervise status` says `attachment_unproven`

This is the fail-closed liveness state.

It means the supervisor can no longer prove fresh live attachment to the current kernel invocation.

Common causes:

- probe acknowledgement missing
- probe nonce mismatch or stale acknowledgement
- bridge registration missing or stale
- active-stage hash mismatch
- supervisor process or bridge process drifted away from the real run

What to do:

1. treat any prior `live_attached` claim as expired
2. inspect the current kernel snapshot:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format json
```

3. inspect the current supervision artifacts:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/bridge_registration.json
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/probe_request.json
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/probe_ack.json
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/heartbeats/
```

4. if no active supervisor is still running, re-enter with:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id <RUN_ID>
```

Important: do **not** assume the kernel is dead just because attachment became unproven. `attachment_unproven` means “not currently provable,” not “definitely stopped.”

## Symptom: `supervise status` says `terminal_observed`

This means the supervisor observed a completed or parked end state and wrote a terminal heartbeat.

Possible meanings:

- the supervised invocation finished cleanly
- the run reached a parked non-recoverable escalated case
- the run reached a final passed terminal

What to do:

1. inspect the nested kernel status in `supervise status --format json`
2. inspect `status --run-id ... --format json`
3. inspect the latest intervention record under:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/interventions/
```

If the run parked on a non-recoverable case, inspect the current escalation bundle before deciding on any manual/off-workflow repair.

## Symptom: `supervise status` says `snapshot_only`

This means there is no fresh supervisory evidence available.

Typical cases:

- the run was never started under supervision
- the last waiting heartbeat went stale
- supervision artifacts are missing or too old to represent current live truth
- you are inspecting a historical run after supervision already ended

What to do:

- rely on `status`, `doctor`, and `run_state.json` for kernel truth
- if you need live supervision for a saved run, re-enter with:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id <RUN_ID>
```

## Symptom: `status` says `waiting`

This still means the kernel stopped on purpose.

Possible causes:

- `awaiting_human_gate`
- `blocked_external`

What to do:

- if you care about live/operator truth, inspect `supervise status` first
- then inspect `status` and `doctor`
- then perform the correct manual-gate or evidence action

## Symptom: `status` says `warning`

This still usually means the run is usable, but a provenance artifact needs attention.

Current warning class:

- saved `runtime_policy.json` missing
- saved `runtime_policy.json` no longer matches the run state

What to do:

1. treat this as an auditability issue, not a reason to rewrite history
2. inspect `status --run-id ... --format json`
3. inspect `doctor --run-id ... --format json`
4. do not fabricate a replacement `runtime_policy.json`

## Symptom: `status` says `error`

This still means the run is not in a healthy operating state.

Common causes:

- escalated item
- missing run-local artifacts
- missing referenced worktree
- missing branch or checkpoint ref

What to do:

1. inspect `status`
2. inspect `doctor`
3. if the issue is deterministic local orchestrator state, try:

```bash
python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --fix-safe \
  --format json
```

4. if the issue is escalated, inspect the escalation manifest before deciding what to do next

## Symptom: `doctor --fix-safe` did not fix everything

That is still expected in some cases.

`doctor --fix-safe` remains intentionally narrow:

- it may rebuild `normalized_plan.json`
- it may report stale refs or orphaned worktrees
- it does not rerun AI stages
- it does not recreate historical provenance artifacts
- it does not modify tracked repo content

The supervisor reuses this exact boundary. It does not widen it.

## Symptom: blocked external evidence is present but the supervisor did not resume

Check these conditions:

1. the inbox or explicit evidence directory actually exists
2. the directory is non-empty
3. the same evidence-package hash has not already been retried for the same blocked fingerprint
4. the run is still genuinely at `blocked_external`

Helpful commands:

```bash
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

## Symptom: a manual gate was approved, but the supervisor did not continue

This can be correct.

The supervisor only auto-resumes after an already-recorded approval when the current resume semantics make that continuation truthful.

If you approved a single-item supervised run and want to keep going at run level, the correct next step is usually:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id <RUN_ID>
```

## Inspection order

When in doubt, inspect in this order:

1. `supervise status --run-id ...` for live/operator truth
2. `status --run-id ...`
3. `doctor --run-id ...`
4. `run_state.json`
5. the current item's `latest_paths`
6. `manual_gate.json` or `escalation_manifest.json`
7. model reports under `.local/ai/plan_orchestrator/runs/<RUN_ID>/`
8. raw supervision artifacts under `.local/automation/plan_orchestrator/runs/<RUN_ID>/supervision/`
