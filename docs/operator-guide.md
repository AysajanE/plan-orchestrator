# Operator Guide

This guide covers the reusable core runtime shipped in `plan-orchestrator`.

For a quick architecture read before going deep on commands and run artifacts, open:

- `docs/assets/plan_orchestrator_workflow.png`
- `docs/plan_orchestrator_workflow.html`
- `docs/release-checklist.md`
- `docs/troubleshooting.md`

## Runtime invariants

The public v1 engine preserves these invariants:

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

## Intentional package layout

The repo intentionally keeps:

- `automation/plan_orchestrator/`
- `automation/run_plan_orchestrator.py`

That layout is part of the public v1 packaging decision so the repo stays checkout-runnable and prompt/schema asset paths stay stable.

## Preflight expectations

Before a fresh run or resume, the runtime expects:

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

If those are present, the runtime raises a preflight error unless you explicitly set:

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

## Commands

### Runtime Policy Control Plane

The playbook remains the source of truth for item semantics such as `allowed_write_roots`,
`requires_red_green`, manual gates, and external checks.

Runtime policy can now be tuned with an optional repo-local control plane:

```json
{
  "schema_version": "plan_orchestrator.control_plane.v1",
  "runtime_policy": {
    "codex_model": "gpt-5.4",
    "max_fix_rounds": 3,
    "audit_timeout_sec": 1800
  }
}
```

By default the runtime reads `plan_orchestrator.json` from the repo root.
You can apply an extra JSON overlay at run time with `run --config ...`.

Precedence is:

1. code defaults
2. repo `plan_orchestrator.json`
3. `run --config <path>`
4. compatibility env vars such as `PLAN_ORCHESTRATOR_CODEX_MODEL`
5. explicit CLI flags such as `--auto-advance` and `--max-items`

This means `auto_advance` is no longer controlled only by the command line.
If the repo or a run overlay enables it, that becomes the starting default for new runs.

Each new run snapshots the resolved runtime policy to
`.local/automation/plan_orchestrator/runs/<RUN_ID>/runtime_policy.json`
and records its digest plus per-field source map in `run_state.json`.
`status` and `doctor` validate that snapshot as a provenance artifact.
If it is missing or no longer matches the saved run state, they report a warning,
but the run can still continue from `run_state.json`.

List items:

```bash
python automation/run_plan_orchestrator.py list-items \
  --playbook path/to/playbook.md
```

Show one item:

```bash
python automation/run_plan_orchestrator.py show-item \
  --playbook path/to/playbook.md \
  --item 01
```

Run diagnostics and validation checks:

```bash
python automation/run_plan_orchestrator.py doctor \
  --playbook path/to/playbook.md \
  --format json
```

Repair deterministic run-local artifacts when a saved run is missing its normalized plan or has stale local references:

```bash
python automation/run_plan_orchestrator.py doctor \
  --run-id RUN_20260325T120000Z_deadbeef \
  --fix-safe \
  --format json
```

`doctor --fix-safe` only rebuilds deterministic local orchestrator artifacts such as
`normalized_plan.json`. It does not rewrite tracked repo files, rerun model stages,
or recreate historical provenance artifacts such as `runtime_policy.json`.

Inspect one saved run:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id RUN_20260325T120000Z_deadbeef \
  --format json
```

List all saved runs:

```bash
python automation/run_plan_orchestrator.py status \
  --all \
  --format text
```

Run the first unfinished item:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --next
```

Run with an explicit runtime-policy overlay:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --item 01 \
  --config ops/runtime-policy.json
```

Run a named item:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --item 01
```

Run multiple items in explicit order:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --items 01,02,03
```

Resume a prior run:

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id RUN_20260325T120000Z_deadbeef
```

Refresh a saved run onto a descendant branch and rebuild the normalized plan from the saved snapshot:

```bash
python automation/run_plan_orchestrator.py refresh-run \
  --run-id RUN_20260325T120000Z_deadbeef \
  --retarget-run-branch-to main
```

Record a manual-gate approval or rejection:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id RUN_20260325T120000Z_deadbeef \
  --item 01 \
  --decision approved \
  --by "Reviewer Name" \
  --note "Required review completed." \
  --evidence-path docs/reviews/signoff.md
```

## Neutral example package

A neutral example adapter and self-contained example playbook family ship under:

```text
examples/basic_markdown_playbook/
```

The example demonstrates:

- one item that should end in `awaiting_human_gate`,
- one item with explicit Red/Green verification,
- one item that stops in `blocked_external` until local evidence is provided.

See `examples/basic_markdown_playbook/README.md` for the walkthrough.

## Local artifact layout

Run-control artifacts:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/
```

That directory now also includes `runtime_policy.json`, the resolved runtime-policy snapshot for the run.

Model JSON reports:

```text
.local/ai/plan_orchestrator/runs/<RUN_ID>/
```

Per-item worktrees:

```text
.local/automation/plan_orchestrator/worktrees/<RUN_ID>/item-<ITEM_ID>-attempt-<N>/
```

Worktree-visible packet for local artifacts:

```text
<WORKTREE>/.local/plan_orchestrator/packet/
```

## External evidence workflow

Some items are blocked by design until a human supplies current evidence.
The runtime does **not** browse the web to satisfy those gates.

When an item requires external evidence, provide a directory when you run or resume that item:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --item 04 \
  --external-evidence-dir /absolute/path/to/evidence
```

The runtime copies those files into the canonical run dir, hashes them, and mirrors them into the worktree packet.

## Manual-gate workflow

When an item ends in `awaiting_human_gate`:

1. inspect the terminal bundle,
2. complete the human review or signoff,
3. record the decision with `mark-manual-gate`,
4. if approved, the runtime marks the item `passed` and fast-forwards the local run branch,
5. if rejected, the item becomes `escalated`.

## Resume semantics

Resume trusts `run_state.json`.

If a worktree is missing or stale, v1 does **not** rewrite history.
It creates a new item attempt from the current integrated run-branch head.

## Auto-advance semantics

Auto-advance is intentionally boring.

It may select the next unfinished item only when:

- all prerequisites are already `passed`,
- no earlier unfinished item is waiting at a manual gate,
- no earlier unfinished item is `blocked_external`,
- no earlier unfinished item is `escalated`.

## Failure inspection

If an item ends in a non-pass terminal state, inspect first:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/items/<ITEM_ID>/attempt-<N>/escalation_manifest.json
```

Then query the run surface:

```bash
python automation/run_plan_orchestrator.py status \
  --run-id <RUN_ID> \
  --format json

python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --format json

python automation/run_plan_orchestrator.py doctor \
  --run-id <RUN_ID> \
  --fix-safe \
  --format json
```

Then inspect:

- `run_state.json`
- the latest `artifact_manifest.*.json` or `audit_packet_manifest.*.json`
- the latest model report under `.local/ai/plan_orchestrator/runs/<RUN_ID>/...`
- the preserved worktree path recorded in the escalation manifest

`doctor --fix-safe` only repairs deterministic local orchestrator state.
It may rebuild `normalized_plan.json` from the saved playbook snapshot and report stale worktrees or refs.
It does not rerun model stages, rewrite tracked repo content, or auto-resolve manual or external gates.
