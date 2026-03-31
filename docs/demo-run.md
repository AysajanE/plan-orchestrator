# Launch Demo and Proof Capture

This runbook defines the exact proof surface the Show HN launch should use.

## Goal

Capture three things from the repo itself:

1. a no-credential tour
2. one happy-path run that ends in `passed`
3. two short control-path runs:
   - `awaiting_human_gate`
   - `blocked_external` followed by `resume --external-evidence-dir ...`

The primary launch demo playbook is `examples/launch_demo_playbook/playbook.md`.
The deeper behavioral example stays in `examples/basic_markdown_playbook/playbook.md`.

## Prerequisites

The no-credential commands only need Python and the repo checkout.

The full run commands below assume:

- a bash-compatible shell
- a clean tracked checkout
- `git`, `bash`, `codex`, and `claude` in `PATH`
- Git identity configured
- no unreviewed ambient agent config unless intentionally acknowledged

If you are running on a workstation where you have intentionally reviewed ambient agent config and want to acknowledge it for the full-run commands below, export the documented override first:

```bash
export PLAN_ORCHESTRATOR_CLEAN_ENV_CONFIRMED=1
```

Do not set that override for the no-credential inspection commands. Use it only when you have reviewed the environment and intentionally want the runtime to proceed.

## Tier 1: no-credential tour

```bash
python automation/run_plan_orchestrator.py list-items \
  --playbook examples/launch_demo_playbook/playbook.md

python automation/run_plan_orchestrator.py show-item \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 01 \
  --format text
```

Optional deeper proof of behavioral wiring:

```bash
python automation/run_plan_orchestrator.py show-item \
  --playbook examples/basic_markdown_playbook/playbook.md \
  --item 02 \
  --format json
```

Expected result for the deeper behavioral inspection:

- `requires_red_green` is `true`
- `verification_hints.required_commands` includes `python examples/basic_markdown_playbook/workspace/tests/test_service.py`

Optional preflight proof before running the full flow:

```bash
python automation/run_plan_orchestrator.py doctor \
  --playbook examples/launch_demo_playbook/playbook.md \
  --format json
```

## Tier 2: happy path on item `01`

The launch demo includes tiny verification scripts under `examples/launch_demo_playbook/workspace/tests/`.
They are intentionally written against the post-run deliverables, so they are expected to fail against the draft files until the runtime updates them.

```bash
RUN_OUTPUT="$(python automation/run_plan_orchestrator.py run \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 01)"
printf '%s\n' "$RUN_OUTPUT"

RUN_ID="$(printf '%s\n' "$RUN_OUTPUT" | python -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')"
echo "RUN_ID=$RUN_ID"

python automation/run_plan_orchestrator.py status \
  --run-id "$RUN_ID" \
  --format json
```

Expected result:

- the returned JSON reports `last_terminal_state` as `passed`
- `run_state.json` reports `current_state` as `ST130_PASSED`
- item `01` has non-null execution, verification, audit, triage, and artifact-manifest paths

Capture the run tree:

```bash
find ".local/automation/plan_orchestrator/runs/$RUN_ID" -maxdepth 4 -type f | sort
find ".local/ai/plan_orchestrator/runs/$RUN_ID" -maxdepth 4 -type f | sort
```

## Control path: item `02` manual gate

```bash
GATE_OUTPUT="$(python automation/run_plan_orchestrator.py run \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 02)"
printf '%s\n' "$GATE_OUTPUT"

GATE_RUN_ID="$(printf '%s\n' "$GATE_OUTPUT" | python -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')"
echo "GATE_RUN_ID=$GATE_RUN_ID"

python automation/run_plan_orchestrator.py status \
  --run-id "$GATE_RUN_ID" \
  --format json

python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id "$GATE_RUN_ID" \
  --item 02 \
  --decision approved \
  --by "Show HN Demo Reviewer" \
  --note "Launch-demo signoff completed." \
  --evidence-path examples/launch_demo_playbook/workspace/docs/runbooks/review_note.md

python automation/run_plan_orchestrator.py status \
  --run-id "$GATE_RUN_ID" \
  --format json
```

Expected result:

- the first command reports `last_terminal_state` as `awaiting_human_gate`
- item `02` writes `manual_gate.json` under its attempt control directory
- the approval command reports `decision` as `approved` and moves the item to `passed`

## Control path: item `03` blocked external, then resume

```bash
BLOCKED_OUTPUT="$(python automation/run_plan_orchestrator.py run \
  --playbook examples/launch_demo_playbook/playbook.md \
  --item 03)"
printf '%s\n' "$BLOCKED_OUTPUT"

BLOCKED_RUN_ID="$(printf '%s\n' "$BLOCKED_OUTPUT" | python -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')"
echo "BLOCKED_RUN_ID=$BLOCKED_RUN_ID"

python automation/run_plan_orchestrator.py status \
  --run-id "$BLOCKED_RUN_ID" \
  --format json

python automation/run_plan_orchestrator.py resume \
  --run-id "$BLOCKED_RUN_ID" \
  --external-evidence-dir examples/launch_demo_playbook/external_evidence

python automation/run_plan_orchestrator.py status \
  --run-id "$BLOCKED_RUN_ID" \
  --format json
```

Expected result:

- the first command reports `last_terminal_state` as `blocked_external`
- item `03` writes `escalation_manifest.json` under `attempt-1`
- the resume command reruns item `03` as a fresh attempt with the supplied evidence packet
- the resumed attempt should create `attempt-2` paths and end in `passed`

## Proof assets to keep

Recommended checked-in files for the visual proof surface in this repo:

- `docs/assets/show-hn-demo/proof-captures.html`
- `docs/assets/show-hn-demo/happy-path-run.png`
- `docs/assets/show-hn-demo/manual-gate.png`
- `docs/assets/show-hn-demo/blocked-external.png`

Optional richer assets such as a no-credential screenshot, an artifact-tree capture, or a GIF are fine, but only add them if you also update `docs/launch-proof.md` to point at the exact checked-in filenames.

Recommended JSON and text excerpts to quote or screenshot:

- `.local/automation/plan_orchestrator/runs/$RUN_ID/run_state.json`
- the item `01` `latest_paths` block from that run state
- the file reported as `verification_report_path`
- the file reported as `artifact_manifest_path`
- the file reported as `triage_report_path`
- `.local/automation/plan_orchestrator/runs/$RUN_ID/items/01/attempt-1/passed_summary.md`
- `.local/automation/plan_orchestrator/runs/$GATE_RUN_ID/items/02/attempt-1/manual_gate.json`
- `.local/automation/plan_orchestrator/runs/$BLOCKED_RUN_ID/items/03/attempt-1/escalation_manifest.json`

Keep the excerpts short. The point is to prove the runtime shape, not to dump raw logs.
Do not hand-edit model reports to make them look cleaner. If a report is noisy, crop or quote a small excerpt and keep the raw file nearby.
