from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from automation.plan_orchestrator import cli


def make_item() -> dict:
    return {
        "item_id": "01",
        "phase": "release note",
        "phase_slug": "release-note",
        "action": "Draft the release note.",
        "why_now": "Need a pass-first demo.",
        "owner_type": "operator",
        "prerequisite_item_ids": [],
        "change_profile": "docs_only",
        "execution_mode": "codex",
        "requires_red_green": False,
        "manual_gate": {
            "required": True,
            "gate_type": "signoff",
            "gate_reason": "Reviewer signoff required.",
            "required_evidence": ["signed note"],
        },
        "external_check": {
            "required": True,
            "mode": "human_supplied_evidence_required",
            "dependencies": ["provider snapshot"],
        },
        "allowed_write_roots": ["docs/runbooks"],
        "repo_surface_paths": ["docs/reference/voice.md"],
        "consult_paths": ["docs/reference/voice.md"],
        "deliverable_paths": ["docs/runbooks/release_note.md"],
        "support_section_ids": ["sec1_plan-context"],
        "verification_hints": {
            "required_commands": ["python tests/test_release_note.py"],
            "suggested_commands": [],
            "required_artifacts": ["docs/runbooks/release_note.md"],
        },
        "notes": ["Keep the note short."],
    }


class CliTests(unittest.TestCase):
    def test_show_item_text_format_renders_human_readable_output(self) -> None:
        fake_orchestrator = mock.Mock()
        fake_orchestrator.show_item.return_value = make_item()

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.PlanOrchestrator",
            return_value=fake_orchestrator,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "show-item",
                        "--playbook",
                        "playbook.md",
                        "--item",
                        "01",
                        "--format",
                        "text",
                    ]
                )

        rendered = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("ITEM 01", rendered)
        self.assertIn("Phase: release note (release-note)", rendered)
        self.assertIn("Manual gate: signoff (Reviewer signoff required.)", rendered)
        self.assertIn(
            "External check: human_supplied_evidence_required (provider snapshot)",
            rendered,
        )
        self.assertIn("Manual gate evidence:", rendered)
        self.assertIn("Required verification commands:", rendered)
        fake_orchestrator.show_item.assert_called_once_with("playbook.md", "01")

    def test_show_item_json_format_still_emits_json(self) -> None:
        fake_orchestrator = mock.Mock()
        fake_orchestrator.show_item.return_value = make_item()

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.PlanOrchestrator",
            return_value=fake_orchestrator,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "show-item",
                        "--playbook",
                        "playbook.md",
                        "--item",
                        "01",
                        "--format",
                        "json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["item_id"], "01")
        self.assertEqual(payload["manual_gate"]["gate_type"], "signoff")
