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

## Helper: print the latest artifact paths for one item

```bash
show_item_paths () {
  python - <<'PY' "$1" "$2"
import json
import pathlib
import sys

run_id, item_id = sys.argv[1], sys.argv[2]
run_state_path = pathlib.Path(".local/automation/plan_orchestrator/runs") / run_id / "run_state.json"
data = json.loads(run_state_path.read_text(encoding="utf-8"))
item = next(entry for entry in data["items"] if entry["item_id"] == item_id)

print(f"run_state_path={run_state_path.as_posix()}")
print(f"current_state={data['current_state']}")
print(f"current_item_id={data['current_item_id']}")
print(f"terminal_state={item['terminal_state']}")
for key, value in item["latest_paths"].items():
    if value:
        print(f"{key}={value}")
PY
}
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

show_item_paths "$RUN_ID" 01
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

show_item_paths "$GATE_RUN_ID" 02

python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id "$GATE_RUN_ID" \
  --item 02 \
  --decision approved \
  --by "Show HN Demo Reviewer" \
  --note "Launch-demo signoff completed." \
  --evidence-path examples/launch_demo_playbook/workspace/docs/runbooks/review_note.md

show_item_paths "$GATE_RUN_ID" 02
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

show_item_paths "$BLOCKED_RUN_ID" 03

python automation/run_plan_orchestrator.py resume \
  --run-id "$BLOCKED_RUN_ID" \
  --external-evidence-dir examples/launch_demo_playbook/external_evidence

show_item_paths "$BLOCKED_RUN_ID" 03
```

Expected result:

- the first command reports `last_terminal_state` as `blocked_external`
- item `03` writes `escalation_manifest.json` under `attempt-1`
- the resume command reruns item `03` as a fresh attempt with the supplied evidence packet
- the resumed attempt should create `attempt-2` paths and end in `passed`

## Proof assets to keep

Recommended checked-in filenames for the visual proof surface:

- `docs/assets/show-hn-demo/no-credential-tour.png`
- `docs/assets/show-hn-demo/happy-path-run.gif`
- `docs/assets/show-hn-demo/happy-path-artifact-tree.png`
- `docs/assets/show-hn-demo/manual-gate.png`
- `docs/assets/show-hn-demo/blocked-external.png`

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
