## 1. Plan Context

Use this example as the launch-oriented demo surface for the repo README and Show HN proof bundle.
Item `01` should reach `passed` cleanly.
Item `02` should stop in `awaiting_human_gate`.
Item `03` should stop in `blocked_external` until local evidence is supplied and the item is resumed.

## 2. Ordered Execution Plan

| step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green | manual_gate | manual_gate_reason | manual_gate_evidence | external_check | external_dependencies | consult_paths | required_verification_commands | required_verification_artifacts | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01 | release note | Turn the release-note draft into a concise launch note for Plan Orchestrator. | Start with a clean pass-first item. | operator | none | `examples/launch_demo_playbook/workspace/docs/runbooks/release_note.md`; `examples/launch_demo_playbook/workspace/docs/reference/voice.md`; `examples/launch_demo_playbook/workspace/tests/test_release_note.py` | `examples/launch_demo_playbook/workspace/docs/runbooks/release_note.md` | Release note passes its verification script and follows the voice reference. | examples/launch_demo_playbook/workspace/docs/runbooks | false | none |  |  | none | none | `examples/launch_demo_playbook/workspace/docs/reference/voice.md`; `examples/launch_demo_playbook/workspace/tests/test_release_note.py` | python examples/launch_demo_playbook/workspace/tests/test_release_note.py | `examples/launch_demo_playbook/workspace/docs/runbooks/release_note.md` | pass-first docs item |
| 02 | review note | Update the review note, then stop for reviewer signoff. | Show the awaiting_human_gate terminal with a small, auditable docs change. | operator | none | `examples/launch_demo_playbook/workspace/docs/runbooks/review_note.md`; `examples/launch_demo_playbook/workspace/docs/reference/review.md`; `examples/launch_demo_playbook/workspace/tests/test_review_note.py` | `examples/launch_demo_playbook/workspace/docs/runbooks/review_note.md` | Review note passes its verification script and then waits for human signoff. | examples/launch_demo_playbook/workspace/docs/runbooks | false | signoff | Human review must approve the review note. | signed review note | none | none | `examples/launch_demo_playbook/workspace/docs/reference/review.md`; `examples/launch_demo_playbook/workspace/tests/test_review_note.py` | python examples/launch_demo_playbook/workspace/tests/test_review_note.py | `examples/launch_demo_playbook/workspace/docs/runbooks/review_note.md` | manual gate demo |
| 03 | status publish | Update the status note from the operator-supplied evidence packet. | Show the blocked_external terminal and the fresh-attempt resume path. | operator | none | `examples/launch_demo_playbook/workspace/docs/runbooks/status_note.md`; `examples/launch_demo_playbook/workspace/docs/reference/status.md`; `examples/launch_demo_playbook/workspace/tests/test_status_note.py` | `examples/launch_demo_playbook/workspace/docs/runbooks/status_note.md` | Status note passes its verification script and uses only the supplied evidence packet. | examples/launch_demo_playbook/workspace/docs/runbooks | false | none |  |  | human_supplied_evidence_required | provider status snapshot | `examples/launch_demo_playbook/workspace/docs/reference/status.md`; `examples/launch_demo_playbook/workspace/tests/test_status_note.py` | python examples/launch_demo_playbook/workspace/tests/test_status_note.py | `examples/launch_demo_playbook/workspace/docs/runbooks/status_note.md` | blocked external demo |

## 3. Phase Details

### 3.1 Release Note

Keep the note short, concrete, and operator-facing.

### 3.2 Review Note

Summarize what changed and leave a clear handoff for the reviewer.

### 3.3 Status Publish

Use only the supplied evidence packet. Do not improvise or browse for missing facts.

## 4. Shared Guidance

### 4.1 Proof Capture

Every item should leave a small, reviewable artifact bundle behind it.

### 4.2 Scope Rules

Never write outside the allowed write roots.

## 5. Risks And Contingencies

Prefer explicit stop states over improvised continuation.

## 6. Immediate Next Actions

Informational only. This section must never create runnable items.
