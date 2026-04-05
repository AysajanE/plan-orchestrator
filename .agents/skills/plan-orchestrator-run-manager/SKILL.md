---
name: plan-orchestrator-run-manager
description: Use when operating, supervising, inspecting, resuming, repairing, or troubleshooting plan-orchestrator runs. Follow the orchestrator's inspect-first workflow and use the supervision surface when live truth matters.
---

# Plan Orchestrator Run Manager

Use this skill when the task is to operate or troubleshoot a `plan-orchestrator` run.

## Core rule

Inspect first. Do not guess from symptoms alone.

For a supervised run, start with the live supervisory surface:

```bash
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID> --format json
```

Then inspect the kernel snapshot surfaces:

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
3. If the run is supervised or live truth matters, run `supervise status` first.
4. Run `status` second.
5. Run `doctor` third.
6. Inspect `run_state.json` only after the command output.
7. Inspect the current item's `latest_paths`.
8. Inspect `manual_gate.json` or `escalation_manifest.json` when present.
9. Inspect model reports under `.local/ai/plan_orchestrator/runs/<RUN_ID>/` only when the run-level view is not enough.
10. Inspect raw supervision artifacts only after the command surfaces.

## Command choice

Use `supervise run` when:

- the goal is a genuinely monitored live operator run
- you want fresh heartbeat and attachment evidence
- you want bounded automatic diagnose/fix/resume behavior around the kernel

Use `supervise resume` when:

- a saved run exists
- you want the supervisor to watch waiting states and recoverable stops
- you want truthful live re-entry to an already-saved run

Use plain `run` when:

- you explicitly want the kernel only
- supervision is not required for this invocation

Use plain `resume` when:

- you explicitly want a direct kernel resume without a long-lived supervisor around it

Use `mark-manual-gate` when:

- the current item is waiting for a human decision

Use `doctor --fix-safe` when:

- local deterministic orchestrator artifacts drifted
- `normalized_plan.json` is missing or invalid
- refs or worktree references need diagnosis

Do not use `doctor --fix-safe` as a general recovery hammer. It does not rerun model stages, recreate historical provenance artifacts, or modify tracked repo files.

## Supervisory status meanings

- `live_attached` — fresh probe evidence proves live attachment
- `waiting_state_observed` — supervisor is still polling, and the kernel is intentionally waiting
- `attachment_unproven` — live attachment can no longer be proven; fail closed
- `terminal_observed` — supervisor observed a terminal completion or parked case
- `snapshot_only` — only saved kernel artifacts are available; no fresh supervisory evidence

## Kernel status meanings

- `ok`: the saved run is healthy
- `warning`: the saved run is usable, but provenance needs attention
- `waiting`: the saved run intentionally stopped for a person or outside evidence
- `error`: the saved run or its local state needs intervention

## Provenance rule

`runtime_policy.json` is still a provenance artifact, not the source of truth.

If it is missing or no longer matches the run state:

- report the warning clearly
- do not fabricate a replacement as if it were original history
- continue to treat `run_state.json` as the authority for actual run behavior

## Human boundary

`awaiting_human_gate` remains the only human-only stop.

The supervisor may wait and later resume after an already-recorded approval, but it must not write the manual-gate decision itself.

## When to open docs

- Open `docs/operator-guide.md` for command and artifact layout details.
- Open `docs/supervision-guide.md` for heartbeat, attachment, and intervention details.
- Open `docs/troubleshooting.md` for symptom-based recovery guidance.
- Open `docs/release-checklist.md` when preparing or supervising a real rollout.
