# Basic Markdown Playbook Example

This example ships two things:

1. a **thin example adapter wrapper** around the canonical `MarkdownPlaybookAdapter`
2. a **small three-item playbook family** that demonstrates the public runtime contract without any source-project vocabulary

## What the example proves

The example is intentionally small, but it shows the full public shape:

- one manual-gate item
- one behavioral item with explicit Red/Green verification wiring
- one item that blocks until human-supplied external evidence is present

The runtime still uses the same core package and the same canonical `markdown_playbook_v1` contract.

## Files

- `playbook.md` — the example `markdown_playbook_v1` file
- `example_adapter.py` — thin wrapper showing where a future job-specific adapter would hook in
- `workspace/` — tracked repo inputs and deliverable surfaces referenced by the playbook
- `external_evidence/` — sample local evidence you can pass to a blocked item

## List the example items

```bash
python automation/run_plan_orchestrator.py list-items \
  --playbook examples/basic_markdown_playbook/playbook.md
```

## Run the manual-gate item

```bash
python automation/run_plan_orchestrator.py run \
  --playbook examples/basic_markdown_playbook/playbook.md \
  --item 01
```

That item is designed to stop in `awaiting_human_gate` after the technical path is clean.

Record the manual decision with:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id RUN_ID_FROM_THE_PREVIOUS_COMMAND \
  --item 01 \
  --decision approved \
  --by "Example Reviewer" \
  --note "Release-note review completed." \
  --evidence-path examples/basic_markdown_playbook/workspace/docs/runbooks/release_note.md
```

## Run the behavioral item

```bash
python automation/run_plan_orchestrator.py run \
  --playbook examples/basic_markdown_playbook/playbook.md \
  --item 02
```

The example command contract for that item is intentionally simple:

```text
python examples/basic_markdown_playbook/workspace/tests/test_service.py
```

It exists to show how a behavioral item declares a required verification command in the playbook.

## Demonstrate blocked-external handling

Run the publication item without evidence first:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook examples/basic_markdown_playbook/playbook.md \
  --item 03
```

That item should stop in `blocked_external`.

Then resume it with the bundled sample evidence directory:

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id RUN_ID_FROM_THE_PREVIOUS_COMMAND \
  --external-evidence-dir examples/basic_markdown_playbook/external_evidence
```

With evidence present, the runtime can continue the item attempt and then apply the normal downstream gates.

## Why this stays domain-neutral

The example uses generic surfaces:

- a release note
- a small service update
- a status note

There are no private names, no source-project identifiers, and no origin-bound workflow assumptions.
