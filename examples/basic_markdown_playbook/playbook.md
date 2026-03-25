## 1. Plan Context

Use this example to validate the public markdown contract and the core runtime loop.

## 2. Ordered Execution Plan

| step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green | manual_gate | manual_gate_reason | manual_gate_evidence | external_check | external_dependencies | consult_paths | required_verification_commands | required_verification_artifacts | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01 | release note | Draft the release note. | Freeze the operator-facing note first. | operator | none | `examples/basic_markdown_playbook/workspace/docs/runbooks/release_note.md`; `examples/basic_markdown_playbook/workspace/docs/reference/voice.md` | `examples/basic_markdown_playbook/workspace/docs/runbooks/release_note.md` | Signed note exists. | examples/basic_markdown_playbook/workspace/docs/runbooks | false | signoff | Human review must approve the note. | signed note | none | none | `examples/basic_markdown_playbook/workspace/docs/reference/voice.md` |  | `examples/basic_markdown_playbook/workspace/docs/runbooks/release_note.md` | docs-oriented item |
| 02 | api update | Implement the API change. | Prove the behavioral path. | swe | 01 | `examples/basic_markdown_playbook/workspace/src/service.py`; `examples/basic_markdown_playbook/workspace/tests/test_service.py`; `examples/basic_markdown_playbook/workspace/docs/reference/api.md` | `examples/basic_markdown_playbook/workspace/src/service.py`; `examples/basic_markdown_playbook/workspace/tests/test_service.py` | Service test passes and docs stay aligned. | examples/basic_markdown_playbook/workspace/src;examples/basic_markdown_playbook/workspace/tests | true | none |  |  | none | none | `examples/basic_markdown_playbook/workspace/docs/reference/api.md` | python examples/basic_markdown_playbook/workspace/tests/test_service.py | `examples/basic_markdown_playbook/workspace/tests/test_service.py` | behavioral item |
| 03 | status publish | Publish the current status note. | Requires operator-supplied evidence. | operator | 02 | `examples/basic_markdown_playbook/workspace/docs/runbooks/status_note.md`; `examples/basic_markdown_playbook/workspace/docs/reference/status.md` | `examples/basic_markdown_playbook/workspace/docs/runbooks/status_note.md` | Approved note exists and evidence is attached. | examples/basic_markdown_playbook/workspace/docs/runbooks | false | approval | Operator must approve the publication. | approval record | human_supplied_evidence_required | current provider status page | `examples/basic_markdown_playbook/workspace/docs/reference/status.md` |  | `examples/basic_markdown_playbook/workspace/docs/runbooks/status_note.md` | waits on external evidence |

## 3. Phase Details

### 3.1 Release Note

Use the approved voice and keep the note narrow.

### 3.2 API Update

Prefer the smallest test-driven implementation.

### 3.3 Status Publish

The publication step must stay local/offline-first.

## 4. Shared Guidance

### 4.1 Review Checklist

Every item should produce a reviewable artifact bundle.

### 4.2 Scope Rules

Never write outside the allowed write roots.

## 5. Risks And Contingencies

Stop cleanly at manual gates or external blockers.

## 6. Immediate Next Actions

Informational only. This section must never create runnable items.
