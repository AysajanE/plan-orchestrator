# Basic Markdown Playbook Example

This is the deeper, dependency-ordered example for `plan-orchestrator`.

If you want the shortest launch demo, start with `examples/launch_demo_playbook/`. This folder is for the fuller control-flow story: item `01` stops at a manual gate, item `02` is the behavioral Red/Green item, and item `03` demonstrates the blocked-external resume path.

## What this example proves

- a manual-gate item can stop cleanly in `awaiting_human_gate`
- a later behavioral item can run only after prerequisites are actually `passed`
- a later item can stop in `blocked_external` and resume from the same run once local evidence is supplied
- a final approval can still be recorded after an external-evidence resume

The dependency order in `playbook.md` matters:

- `02` depends on `01`
- `03` depends on `02`

That is why the walkthrough below intentionally keeps a single `RUN_ID` alive across `run`, `mark-manual-gate`, and `resume`.

## Files

- `playbook.md` — the example `markdown_playbook_v1` file
- `example_adapter.py` — thin wrapper showing where a future job-specific adapter would hook in
- `workspace/` — tracked repo inputs and deliverable surfaces referenced by the playbook
- `external_evidence/` — sample local evidence for the blocked-external resume step

## Read-only inspection

List the example items:

```bash
python automation/run_plan_orchestrator.py list-items \
  --playbook examples/basic_markdown_playbook/playbook.md
```

Inspect the behavioral item and its verification wiring:

```bash
python automation/run_plan_orchestrator.py show-item \
  --playbook examples/basic_markdown_playbook/playbook.md \
  --item 02 \
  --format json
```

## Exact sequential walkthrough

The commands below assume a bash-compatible shell and a clean tracked checkout.

If you are running this full walkthrough on a workstation with intentionally reviewed ambient Codex or Claude config, acknowledge the preflight check first:

```bash
export PLAN_ORCHESTRATOR_CLEAN_ENV_CONFIRMED=1
```

Do not set that override for the read-only inspection commands above. Use it only when you have reviewed the local environment and intentionally want the full runtime path to proceed.

### 1) Start item `01`

```bash
RUN_OUTPUT="$(python automation/run_plan_orchestrator.py run \
  --playbook examples/basic_markdown_playbook/playbook.md \
  --item 01)"
printf '%s\n' "$RUN_OUTPUT"

RUN_ID="$(printf '%s\n' "$RUN_OUTPUT" | python -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')"
echo "RUN_ID=$RUN_ID"
```

Expected result:

- `last_terminal_state` is `awaiting_human_gate`
- the run now waits for a human decision on item `01`

### 2) Approve the manual gate for item `01`

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id "$RUN_ID" \
  --item 01 \
  --decision approved \
  --by "Example Reviewer" \
  --note "Release-note review completed." \
  --evidence-path examples/basic_markdown_playbook/workspace/docs/runbooks/release_note.md
```

Expected result:

- item `01` becomes `passed`
- the run branch fast-forwards to the reviewed checkpoint for item `01`

### 3) Resume the same run to execute item `02`

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id "$RUN_ID"
```

Expected result:

- item `02` runs because its prerequisite is now truly `passed`
- the returned JSON reports `last_terminal_state` as `passed`

### 4) Resume the same run again to demonstrate `blocked_external` on item `03`

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id "$RUN_ID"
```

Expected result:

- item `03` stops in `blocked_external`
- the returned JSON reports `last_terminal_state` as `blocked_external`

### 5) Resume item `03` with the bundled sample evidence

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id "$RUN_ID" \
  --external-evidence-dir examples/basic_markdown_playbook/external_evidence
```

Expected result:

- item `03` reruns as a fresh attempt with the supplied evidence packet
- the returned JSON should now report `last_terminal_state` as `awaiting_human_gate`

### 6) Approve the final manual gate for item `03`

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id "$RUN_ID" \
  --item 03 \
  --decision approved \
  --by "Example Reviewer" \
  --note "Status-note approval completed." \
  --evidence-path examples/basic_markdown_playbook/external_evidence/provider_status_snapshot.md
```

Expected result:

- item `03` becomes `passed`
- the run has now exercised manual gate, behavioral verification, external evidence, and resume semantics in one dependency-ordered flow

## Why this stays domain-neutral

The example uses generic surfaces:

- a release note
- a small service update
- a status note

There are no private names, no source-project identifiers, and no origin-bound workflow assumptions.
