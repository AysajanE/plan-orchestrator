from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from automation.plan_orchestrator.adapters import MarkdownPlaybookAdapter
from automation.plan_orchestrator.playbook_parser import parse_playbook, section_by_number


PLAYBOOK_FIXTURE = textwrap.dedent(
    """
    ## 1. Plan Context

    Freeze the approved plan context before any item runs.

    ## 2. Ordered Execution Plan

    | step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green | manual_gate | manual_gate_reason | manual_gate_evidence | external_check | external_dependencies | consult_paths | required_verification_commands | required_verification_artifacts | notes |
    | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
    | 01 | release note | Draft the release note. | Freeze the operator-facing note first. | operator | none | `docs/runbooks/release_note.md`; `docs/reference/voice.md` | `docs/runbooks/release_note.md` | Signed note exists. | docs/runbooks | false | signoff | Human review must approve the note. | signed note | none | none | `docs/reference/voice.md` |  | `docs/runbooks/release_note.md` | docs-only item |
    | 02 | api update | Implement the API change. | Prove the behavioral path. | swe | 01 | `src/service.py`; `tests/test_service.py`; `docs/reference/api.md` | `src/service.py`; `tests/test_service.py` | Tests pass and docs stay aligned. | src;tests | true | none |  |  | none | none | `docs/reference/api.md` | `pytest -q tests/test_service.py` | `tests/test_service.py` | behavioral item |
    | 03 | status publish | Publish the current status note. | Requires operator-supplied evidence. | operator | 02 | `docs/runbooks/status_note.md`; `docs/reference/status.md` | `docs/runbooks/status_note.md` | Approved note exists and evidence is attached. | docs/runbooks | false | approval | Operator must approve the publication. | approval record | human_supplied_evidence_required | current provider status page | `docs/reference/status.md` |  | `docs/runbooks/status_note.md` | waits on external evidence |

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
    """
).strip() + "\n"


class PlaybookParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name)
        self.playbook_path = self.repo_root / "playbook.md"
        self.playbook_path.write_text(PLAYBOOK_FIXTURE, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_parse_playbook_extracts_ordered_execution_rows(self) -> None:
        parsed = parse_playbook(self.playbook_path)

        self.assertEqual(
            [row["step_id"] for row in parsed["ordered_execution_rows"]],
            ["01", "02", "03"],
        )
        section_two = section_by_number(parsed, "2")
        self.assertEqual(section_two["title"], "Ordered Execution Plan")
        self.assertTrue(parsed["sha256"])

    def test_parse_playbook_normalizes_numbered_h3_subsections(self) -> None:
        parsed = parse_playbook(self.playbook_path)
        phase_details = section_by_number(parsed, "3")

        self.assertEqual(
            [subsection["title"] for subsection in phase_details["subsections"]],
            ["Release Note", "API Update", "Status Publish"],
        )
        self.assertEqual(
            [subsection["slug"] for subsection in phase_details["subsections"]],
            ["release-note", "api-update", "status-publish"],
        )

    def test_markdown_adapter_normalizes_public_contract(self) -> None:
        parsed = parse_playbook(self.playbook_path)
        adapter = MarkdownPlaybookAdapter(self.repo_root)
        plan = adapter.normalize(parsed, self.playbook_path)

        self.assertEqual(plan.adapter_id, "markdown_playbook_v1")
        self.assertEqual(plan.plan_source["source_kind"], "markdown_playbook_v1")
        self.assertEqual(
            [item.item_id for item in plan.items],
            ["01", "02", "03"],
        )

        item01 = plan.get_item("01")
        self.assertEqual(item01.change_profile, "docs_only")
        self.assertFalse(item01.requires_red_green)
        self.assertEqual(item01.execution_mode, "codex")
        self.assertEqual(item01.host_commands, [])
        self.assertEqual(item01.allowed_write_roots, ["docs/runbooks"])
        self.assertTrue(item01.manual_gate["required"])
        self.assertEqual(item01.manual_gate["gate_type"], "signoff")
        self.assertEqual(item01.external_check["required"], False)
        self.assertEqual(
            item01.verification_hints["required_artifacts"],
            ["docs/runbooks/release_note.md"],
        )
        self.assertEqual(item01.verification_hints["required_commands"], [])
        self.assertIn("sec1_plan-context", item01.support_section_ids)
        self.assertIn("sec3_release-note", item01.support_section_ids)
        self.assertIn("sec4_review-checklist", item01.support_section_ids)
        self.assertIn("sec5_risks-and-contingencies", item01.support_section_ids)
        self.assertNotIn("sec6_immediate-next-actions", item01.support_section_ids)

        item02 = plan.get_item("02")
        self.assertEqual(item02.change_profile, "behavioral_code")
        self.assertTrue(item02.requires_red_green)
        self.assertEqual(item02.prerequisite_item_ids, ["01"])
        self.assertEqual(item02.allowed_write_roots, ["src", "tests"])
        self.assertEqual(
            item02.verification_hints["required_commands"],
            ["pytest -q tests/test_service.py"],
        )
        self.assertEqual(
            item02.verification_hints["required_artifacts"],
            ["tests/test_service.py"],
        )

        item03 = plan.get_item("03")
        self.assertFalse(item03.requires_red_green)
        self.assertTrue(item03.manual_gate["required"])
        self.assertEqual(item03.manual_gate["gate_type"], "approval")
        self.assertTrue(item03.external_check["required"])
        self.assertEqual(
            item03.external_check["mode"],
            "human_supplied_evidence_required",
        )
        self.assertEqual(
            item03.external_check["dependencies"],
            ["current provider status page"],
        )
        self.assertIn("sec3_status-publish", item03.support_section_ids)

    def test_markdown_adapter_rejects_authored_change_profile_override(self) -> None:
        bad_playbook = self.playbook_path.read_text(encoding="utf-8").replace(
            "requires_red_green | manual_gate",
            "requires_red_green | change_profile | manual_gate",
        ).replace(
            "| false | signoff | Human review must approve the note. |",
            "| false | docs_only | signoff | Human review must approve the note. |",
            1,
        )
        self.playbook_path.write_text(bad_playbook, encoding="utf-8")

        parsed = parse_playbook(self.playbook_path)
        adapter = MarkdownPlaybookAdapter(self.repo_root)

        with self.assertRaisesRegex(ValueError, "change_profile"):
            adapter.normalize(parsed, self.playbook_path)

    def test_markdown_adapter_rejects_reserved_execution_columns_deterministically(self) -> None:
        bad_playbook = self.playbook_path.read_text(encoding="utf-8").replace(
            "requires_red_green | manual_gate",
            "requires_red_green | execution_mode | host_commands | manual_gate",
        ).replace(
            "| false | signoff | Human review must approve the note. |",
            "| false | host_command | `make release-note` | signoff | Human review must approve the note. |",
            1,
        )
        self.playbook_path.write_text(bad_playbook, encoding="utf-8")

        parsed = parse_playbook(self.playbook_path)
        adapter = MarkdownPlaybookAdapter(self.repo_root)

        with self.assertRaisesRegex(
            ValueError,
            r"reserved authored columns are not allowed.*execution_mode, host_commands",
        ):
            adapter.normalize(parsed, self.playbook_path)

    def test_markdown_adapter_requires_verification_commands_for_red_green_items(self) -> None:
        bad_playbook = self.playbook_path.read_text(encoding="utf-8").replace(
            "`pytest -q tests/test_service.py`",
            "",
            1,
        )
        self.playbook_path.write_text(bad_playbook, encoding="utf-8")

        parsed = parse_playbook(self.playbook_path)
        adapter = MarkdownPlaybookAdapter(self.repo_root)

        with self.assertRaisesRegex(ValueError, "requires at least one required_verification_commands entry"):
            adapter.normalize(parsed, self.playbook_path)
