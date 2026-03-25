# plan-orchestrator

Stdlib-first Python orchestrator for running approved plan items one at a time in isolated git worktrees.

The core runtime is intentionally boring:

- one orchestrator-owned worktree per item attempt
- direct ingestion of one approved `markdown_playbook_v1` file
- normalization into `normalized_plan.json` before any item executes
- `codex exec` as the mutation lane for execute, fix, and remediation
- verification before either audit lane
- dual audit over a frozen audit packet:
  - `codex exec`
  - `claude -p`
- deterministic merged findings before triage
- bounded fix and remediation loops
- explicit `awaiting_human_gate`, `blocked_external`, and `escalated` terminals
- conservative auto-advance to the next unfinished item only after a clean pass

## Intentional v1 layout

This public repo intentionally keeps the engine under `automation/plan_orchestrator/` and the launcher at `automation/run_plan_orchestrator.py`.

That is a deliberate packaging choice for v1:

- the repo stays checkout-runnable without an installation step,
- prompt and schema assets keep stable relative paths,
- operator commands stay short and explicit,
- the transfer package stays close to the proven in-repo implementation.

## Canonical input contract

The public v1 authoring contract is `markdown_playbook_v1`.

You provide one parser-safe markdown file with a required `## 2. Ordered Execution Plan` pipe table keyed by `step_id`. The runtime snapshots that markdown, normalizes it into `normalized_plan.json`, and executes the normalized plan.

See:

- `docs/playbook-contract.md`
- `docs/operator-guide.md`

## Quick start

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

Run the first unfinished item:

```bash
python automation/run_plan_orchestrator.py run \
  --playbook path/to/playbook.md \
  --next
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

Record a manual-gate decision:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id RUN_20260325T120000Z_deadbeef \
  --item 01 \
  --decision approved \
  --by "Reviewer Name" \
  --note "Required review completed." \
  --evidence-path docs/reviews/signoff.md
```

## Neutral example

A thin example adapter and a self-contained example playbook family ship under `examples/basic_markdown_playbook/`.

List the example items:

```bash
python automation/run_plan_orchestrator.py list-items \
  --playbook examples/basic_markdown_playbook/playbook.md
```

The example demonstrates:

- a manual-gate item that should end in `awaiting_human_gate`,
- a behavioral item with explicit Red/Green verification wiring,
- and an item that stops in `blocked_external` until you provide local evidence.

See `examples/basic_markdown_playbook/README.md` for the exact walkthrough.

## Local artifact layout

Run-control artifacts:

```text
.local/automation/plan_orchestrator/runs/<RUN_ID>/
```

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

## Safety defaults

The runtime is reproducibility-first and local/offline-first by default.

That means:

- no agent-owned git operations
- no implicit web browsing by execution, audit, triage, fix, or remediation
- no automatic continuation past manual gates or external blockers
- no destructive reset/clean/rebase/squash automation
- only a passed item, or an item later approved through a manual gate, can advance the run branch

## Package self-check

After copying the repo into its standalone home, run:

```bash
python -m unittest discover -s automation/plan_orchestrator/tests -t .
```

Capture that command's output as the package verification record for the extracted repo.

## Operator notes

- Verification is a first-class stage. It runs after each mutation stage and before audit.
- Audit always operates on a frozen packet plus candidate patch.
- Findings are merged deterministically before triage.
- Repair loops are bounded by configuration; defaults remain 2 fix rounds plus 1 remediation round.
- External evidence must be supplied as local files. The runtime does not browse the web to satisfy external-evidence gates.
