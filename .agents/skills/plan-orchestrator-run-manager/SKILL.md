---
name: plan-orchestrator-run-manager
description: Use when operating, inspecting, resuming, repairing, or troubleshooting plan-orchestrator runs. Follow the orchestrator's inspect-first workflow before making changes or giving advice about run state.
---

# Plan Orchestrator Run Manager

Use this skill when the task is to operate or troubleshoot a `plan-orchestrator` run.

## Core rule

Inspect first. Do not guess from symptoms alone.

Start with the built-in command surface before reading raw files:

```bash
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

For preflight-only checks before a run exists:

```bash
python automation/run_plan_orchestrator.py doctor --playbook path/to/playbook.md --format json
```

## Inspection order

1. Confirm the repo root and current working tree state.
2. Identify the playbook path or run id.
3. Run `status` first.
4. Run `doctor` second.
5. Inspect `run_state.json` only after the command output.
6. Inspect the current item's `latest_paths`.
7. Inspect `manual_gate.json` or `escalation_manifest.json` when present.
8. Inspect model reports under `.local/ai/plan_orchestrator/runs/<RUN_ID>/` only when the run-level view is not enough.

## Command choice

Use `run` when:

- there is no existing run yet
- the goal is to start a new approved item or queue

Use `resume` when:

- a saved run exists
- the current item is waiting on external evidence or needs a fresh post-escalation attempt

Use `mark-manual-gate` when:

- the current item is waiting for a human decision

Use `doctor --fix-safe` when:

- local deterministic orchestrator artifacts drifted
- `normalized_plan.json` is missing or invalid
- refs or worktree references need diagnosis

Do not use `doctor --fix-safe` as a general recovery hammer. It does not rerun model stages, recreate historical provenance artifacts, or modify tracked repo files.

Use `refresh-run` when:

- the saved run must be retargeted onto a descendant branch
- the normalized plan should be rebuilt from the saved playbook snapshot

## Status meanings

- `ok`: the run is healthy
- `warning`: the run is usable, but provenance needs attention
- `waiting`: the run intentionally stopped for a person or outside evidence
- `error`: the run or its local state needs intervention

## Provenance rule

`runtime_policy.json` is a provenance artifact, not the source of truth.

If it is missing or no longer matches the run state:

- report the warning clearly
- do not fabricate a replacement as if it were original history
- continue to treat `run_state.json` as the authority for actual run behavior

## When to open docs

- Open `docs/operator-guide.md` for command and artifact layout details.
- Open `docs/troubleshooting.md` for symptom-based recovery guidance.
- Open `docs/release-checklist.md` when preparing or supervising a real rollout.
