# Launch Demo Playbook

This is the pass-first demo surface for the repository README and the eventual Show HN launch.

## What it proves

- item `01` reaches `passed`
- item `02` reaches `awaiting_human_gate`
- item `03` reaches `blocked_external`, then passes after `resume --external-evidence-dir ...`

All three items are independent on purpose, so every documented command can be run directly from a fresh clone.

## Files

- `playbook.md` — launch-oriented `markdown_playbook_v1` file
- `workspace/` — tracked inputs, drafts, and tiny verification scripts
- `external_evidence/` — sample local evidence for the blocked-external resume step

## Important note about the verification scripts

The scripts under `workspace/tests/` are intentionally written against the post-run deliverables.
They are part of the proof surface for the full runtime path.
They are not part of repo CI and they are expected to fail against the draft files until the runtime updates them.

## No-credential tour

```bash
python automation/run_plan_orchestrator.py list-items \
  --playbook examples/launch_demo_playbook/playbook.md

python automation/run_plan_orchestrator.py show-item \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 01 \
  --format text
```

## Full-run prerequisite

The full `run`, `mark-manual-gate`, and `resume` walkthrough below assumes a clean tracked checkout.

If you are running it on a workstation with intentionally reviewed ambient Codex or Claude config, acknowledge the preflight check first:

```bash
export PLAN_ORCHESTRATOR_CLEAN_ENV_CONFIRMED=1
```

Do not set that override for the no-credential tour above. Use it only when you have reviewed the local environment and intentionally want the full runtime path to proceed.

## 1) Happy path: item `01`

```bash
RUN_OUTPUT="$(python automation/run_plan_orchestrator.py run \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 01)"
printf '%s\n' "$RUN_OUTPUT"

RUN_ID="$(printf '%s\n' "$RUN_OUTPUT" | python -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')"
echo "RUN_ID=$RUN_ID"
```

Expected result:

- the returned JSON reports `last_terminal_state` as `passed`
- the run state for item `01` ends in `ST130_PASSED`

## 2) Manual gate: item `02`

```bash
GATE_OUTPUT="$(python automation/run_plan_orchestrator.py run \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 02)"
printf '%s\n' "$GATE_OUTPUT"

GATE_RUN_ID="$(printf '%s\n' "$GATE_OUTPUT" | python -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')"
echo "GATE_RUN_ID=$GATE_RUN_ID"

python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id "$GATE_RUN_ID" \
  --item 02 \
  --decision approved \
  --by "Show HN Demo Reviewer" \
  --note "Launch-demo signoff completed." \
  --evidence-path examples/launch_demo_playbook/workspace/docs/runbooks/review_note.md
```

Expected result:

- the first command reports `last_terminal_state` as `awaiting_human_gate`
- the approval command reports `decision` as `approved`

## 3) Blocked external, then resume: item `03`

```bash
BLOCKED_OUTPUT="$(python automation/run_plan_orchestrator.py run \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 03)"
printf '%s\n' "$BLOCKED_OUTPUT"

BLOCKED_RUN_ID="$(printf '%s\n' "$BLOCKED_OUTPUT" | python -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')"
echo "BLOCKED_RUN_ID=$BLOCKED_RUN_ID"

python automation/run_plan_orchestrator.py resume \
  --run-id "$BLOCKED_RUN_ID" \
  --external-evidence-dir examples/launch_demo_playbook/external_evidence
```

Expected result:

- the first command reports `last_terminal_state` as `blocked_external`
- the resume command reruns item `03` as a fresh attempt with local evidence and should end in `passed`

## Next step

Use `docs/demo-run.md` to capture the proof bundle that belongs in the README and the eventual Show HN first comment.
