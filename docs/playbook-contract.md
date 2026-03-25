# `markdown_playbook_v1`

`markdown_playbook_v1` is the canonical public input contract for the core engine.

One reviewed markdown file is the immediate runtime source.
The runtime snapshots that file and normalizes it into `normalized_plan.json` before any item executes.

## Contract summary

- one markdown file
- one required `## 2. Ordered Execution Plan` pipe table
- exact header names
- one row per runnable item
- support sections elsewhere in the same file
- no hidden item-ID lookup tables
- explicit authored safety fields for scope and Red/Green policy

## Authored versus derived fields

| surface | status in `markdown_playbook_v1` | rule |
|---|---|---|
| `allowed_write_roots` | authored | required in the plan table |
| `requires_red_green` | authored | required in the plan table |
| `change_profile` | derived | calculated during normalization |
| `execution_mode` | derived | fixed to `codex` in canonical public v1 |
| `host_commands` | derived / reserved | kept empty in canonical public v1; reserved for a future extension |

That means authors should **not** place `change_profile`, `execution_mode`, or `host_commands` values in the canonical v1 playbook table.
If they appear with values, normalization fails.

## Required H2 section numbering

The parser requires numbered H2 sections.

The canonical public layout is:

1. `## 1. Plan Context`
2. `## 2. Ordered Execution Plan`
3. `## 3. Phase Details`
4. `## 4. Shared Guidance`
5. `## 5. Risks And Contingencies`
6. `## 6. Immediate Next Actions`

Only section **2** is mandatory for execution.
The others are optional support surfaces.

## Required plan table headers

The `## 2. Ordered Execution Plan` table must use these exact headers:

| column | required | meaning |
|---|---|---|
| `step_id` | yes | Stable item identifier used by CLI and run state |
| `phase` | yes | Human-readable phase name |
| `action` | yes | What should be done |
| `why_now` | yes | Why this item should happen now |
| `owner_type` | yes | Human role or lane owner label |
| `prerequisites` | yes | `none`, comma-separated ids, or numeric ranges like `01-03` |
| `repo_surfaces` | yes | Concrete tracked repo inputs for the item |
| `deliverable` | yes | Concrete repo-relative outputs expected from the item |
| `exit_criteria` | yes | Human-readable completion target |
| `allowed_write_roots` | yes | Semicolon-separated repo-relative roots the mutation stage may touch |
| `requires_red_green` | yes | Boolean: `true/false`, `yes/no`, `1/0` |

## Optional plan table headers

| column | meaning |
|---|---|
| `manual_gate` | `none`, `signoff`, `approval`, `operator_confirmation`, `security_review`, `presenter_review`, or `custom` |
| `manual_gate_reason` | Why the human gate exists |
| `manual_gate_evidence` | Semicolon-separated evidence expectations for the human gate |
| `external_check` | `none` or `human_supplied_evidence_required` |
| `external_dependencies` | Semicolon-separated external dependencies or evidence labels |
| `consult_paths` | Additional tracked repo inputs beyond `repo_surfaces` |
| `required_verification_commands` | Semicolon-separated required shell commands |
| `suggested_verification_commands` | Semicolon-separated optional shell commands |
| `required_verification_artifacts` | Semicolon-separated artifacts that verification must check |
| `notes` | Semicolon-separated per-item operator notes |

## Path rules

The runtime expects concrete repo-relative paths in these authored cells:

- `repo_surfaces`
- `deliverable`
- `consult_paths`
- `required_verification_artifacts`

Use slash-delimited repo-relative paths.
Backticks are allowed.
Semicolon-separated lists are allowed.

Examples:

```text
`docs/runbooks/release_note.md`
`src/service.py`; `tests/test_service.py`
ops/artifacts/latest/
```

## Verification rules

For `requires_red_green=true` items:

- at least one `required_verification_commands` entry is required
- those commands become the required verification gate
- write command cells as plain shell commands; if a whole command is wrapped in one pair of Markdown backticks, normalization strips them
- the mutation lane must record real Red/Green evidence

For `requires_red_green=false` items:

- the runtime does not require failing-test evidence
- verification may rely on artifact existence checks and optional command groups
- if `required_verification_artifacts` is omitted, the runtime defaults it to the parsed `deliverable` path list

## Support-section behavior

Support sections are carried into the normalized plan and projected into `item_context`.

The canonical projection rules are:

- `## 1. Plan Context` applies to all items as global context
- `## 3. Phase Details` attaches H3 subsections to items whose `phase` slug matches the subsection title slug
- `## 4. Shared Guidance` applies to all items
- `## 5. Risks And Contingencies` applies to all items
- `## 6. Immediate Next Actions` is informational only and is **not** attached to item execution context by default

Generated support-section ids follow the normalized shape used by the adapter:

- section 1 -> `sec1_plan-context`
- section 3 H3 `### 3.2 API Update` -> `sec3_api-update`
- section 4 H3 `### 4.1 Review Checklist` -> `sec4_review-checklist`
- section 5 -> `sec5_risks-and-contingencies`

## Immediate source and internal boundary

The public runtime boundary is intentionally split in two:

1. **Authored source**: one approved markdown playbook
2. **Runtime source**: one normalized `NormalizedPlan`

That keeps human review on the markdown and runtime control on the normalized manifest.

## Future extension path

The approved extension path is:

- add a validated YAML/JSON loader later,
- compile it into the same `NormalizedPlan`,
- keep the runtime loop unchanged.

The core engine should not gain a second authoritative runtime contract.
