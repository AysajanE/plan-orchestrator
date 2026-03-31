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
    def test_status_help_describes_new_options(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["status", "--help"])

        rendered = " ".join(stdout.getvalue().split())
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("Show one saved run by id.", rendered)
        self.assertIn("Show every saved run under .local/automation/plan_orchestrator/runs.", rendered)
        self.assertIn("Exit with the reported run health code instead of always returning zero.", rendered)

    def test_doctor_help_describes_scope_and_fix_safe_boundary(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["doctor", "--help"])

        rendered = " ".join(stdout.getvalue().split())
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("Optional playbook to parse and normalize without starting a run.", rendered)
        self.assertIn("Optional saved run id to validate and inspect.", rendered)
        self.assertIn("Rebuild deterministic local orchestrator artifacts only; never touches tracked repo files.", rendered)

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

    def test_status_json_format_emits_single_run_payload(self) -> None:
        payload = {
            "run_id": "RUN_STATUS",
            "status_level": "ok",
            "exit_code": 0,
            "current_state": "ST130_PASSED",
        }

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.load_run_status",
            return_value=payload,
            create=True,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "status",
                        "--run-id",
                        "RUN_STATUS",
                        "--format",
                        "json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_status_all_json_format_emits_all_runs_payload(self) -> None:
        payload = [
            {"run_id": "RUN_A", "status_level": "ok", "exit_code": 0},
            {"run_id": "RUN_B", "status_level": "waiting", "exit_code": 1},
        ]

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.list_run_statuses",
            return_value=payload,
            create=True,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "status",
                        "--all",
                        "--format",
                        "json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_status_exit_code_can_follow_summary_health(self) -> None:
        payload = {
            "run_id": "RUN_WAITING",
            "status_level": "waiting",
            "exit_code": 1,
            "current_state": "ST110_AWAITING_HUMAN_GATE",
        }

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.load_run_status",
            return_value=payload,
            create=True,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "status",
                        "--run-id",
                        "RUN_WAITING",
                        "--format",
                        "json",
                        "--exit-code",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_doctor_json_format_emits_diagnostics_payload(self) -> None:
        payload = {
            "ok": True,
            "repo_root": ".",
            "checks": [
                {"name": "agent_environment", "status": "ok"},
                {"name": "playbook_parse", "status": "ok"},
            ],
        }

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.run_doctor",
            return_value=payload,
            create=True,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "doctor",
                        "--playbook",
                        "playbook.md",
                        "--format",
                        "json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_doctor_fix_safe_passes_flag_through_to_runner(self) -> None:
        payload = {
            "ok": True,
            "repo_root": ".",
            "checks": [],
            "repairs": [{"name": "rebuild_normalized_plan", "status": "applied"}],
        }

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.run_doctor",
            return_value=payload,
            create=True,
        ) as fake_doctor:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "doctor",
                        "--run-id",
                        "RUN_FIX",
                        "--fix-safe",
                        "--format",
                        "json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        fake_doctor.assert_called_once_with(
            Path("."),
            playbook_path=None,
            run_id="RUN_FIX",
            fix_safe=True,
        )

    def test_run_passes_config_overlay_to_orchestrator(self) -> None:
        fake_orchestrator = mock.Mock()
        fake_orchestrator.run_new.return_value = {
            "run_id": "RUN_CFG",
            "current_state": "ST05_PLAN_NORMALIZED",
            "current_item_id": "01",
            "last_terminal_state": "passed",
            "run_state_path": ".local/automation/plan_orchestrator/runs/RUN_CFG/run_state.json",
        }

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
                        "run",
                        "--playbook",
                        "playbook.md",
                        "--item",
                        "01",
                        "--config",
                        "ops/runtime-policy.json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        fake_orchestrator.run_new.assert_called_once_with(
            playbook_path="playbook.md",
            item_id="01",
            item_ids=None,
            next_only=False,
            external_evidence_dir=None,
            auto_advance=None,
            max_items=None,
            config_path="ops/runtime-policy.json",
        )
