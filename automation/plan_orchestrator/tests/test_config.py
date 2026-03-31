from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation.plan_orchestrator.config import resolve_runtime_policy
from automation.plan_orchestrator.runtime import PlanOrchestrator
from automation.plan_orchestrator.state_store import load_run_state
from automation.plan_orchestrator.tests.support import make_plan
from automation.plan_orchestrator.validators import ValidationError, compute_sha256, load_json


def write_control_plane(path: Path, runtime_policy: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "plan_orchestrator.control_plane.v1",
                "runtime_policy": runtime_policy,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class RuntimePolicyConfigTests(unittest.TestCase):
    def test_resolve_runtime_policy_honors_precedence_and_tracks_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            write_control_plane(
                repo_root / "plan_orchestrator.json",
                {
                    "codex_model": "repo-codex",
                    "claude_model": "repo-claude",
                    "max_fix_rounds": 4,
                    "auto_advance": False,
                },
            )
            overlay_path = repo_root / "ops" / "runtime-policy.json"
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            write_control_plane(
                overlay_path,
                {
                    "claude_model": "overlay-claude",
                    "max_fix_rounds": 6,
                    "max_items": 3,
                },
            )

            with mock.patch.dict(
                "os.environ",
                {
                    "PLAN_ORCHESTRATOR_CODEX_MODEL": "env-codex",
                    "PLAN_ORCHESTRATOR_AUDIT_TIMEOUT_SEC": "77",
                },
                clear=False,
            ):
                resolution = resolve_runtime_policy(
                    repo_root,
                    config_path=overlay_path,
                    cli_auto_advance=True,
                    cli_max_items=9,
                )

        self.assertEqual(resolution.options.codex_model, "env-codex")
        self.assertEqual(resolution.options.claude_model, "overlay-claude")
        self.assertEqual(resolution.options.max_fix_rounds, 6)
        self.assertEqual(resolution.options.audit_timeout_sec, 77)
        self.assertTrue(resolution.options.auto_advance)
        self.assertEqual(resolution.options.max_items, 9)
        self.assertEqual(resolution.sources["codex_model"], "env")
        self.assertEqual(resolution.sources["claude_model"], "config_file")
        self.assertEqual(resolution.sources["max_fix_rounds"], "config_file")
        self.assertEqual(resolution.sources["audit_timeout_sec"], "env")
        self.assertEqual(resolution.sources["auto_advance"], "cli")
        self.assertEqual(resolution.sources["max_items"], "cli")
        self.assertEqual(resolution.sources["triage_timeout_sec"], "default")

    def test_resolve_runtime_policy_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            write_control_plane(
                repo_root / "plan_orchestrator.json",
                {
                    "codex_model": "repo-codex",
                    "unexpected": 1,
                },
            )

            with self.assertRaises(ValidationError):
                resolve_runtime_policy(repo_root)

    def test_run_new_persists_runtime_policy_snapshot_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            playbook_path.write_text("# playbook\n", encoding="utf-8")
            overlay_path = repo_root / "ops" / "runtime-policy.json"
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            write_control_plane(
                overlay_path,
                {
                    "codex_model": "overlay-codex",
                    "max_fix_rounds": 5,
                },
            )
            orchestrator = PlanOrchestrator(repo_root)
            plan = make_plan()
            parsed = {
                "sha256": "b" * 64,
                "raw_markdown": "# playbook\n",
                "ordered_execution_rows": [],
                "sections": [],
            }
            fake_manager = SimpleNamespace(
                current_head_sha=lambda: "deadbeef",
                ensure_run_branch=lambda run_id, _head: f"orchestrator/run/{run_id}",
            )

            with mock.patch(
                "automation.plan_orchestrator.runtime.make_run_id",
                return_value="RUN_CFG_SNAPSHOT",
            ), mock.patch(
                "automation.plan_orchestrator.runtime.parse_playbook",
                return_value=parsed,
            ), mock.patch.object(
                orchestrator.adapter,
                "normalize",
                return_value=plan,
            ), mock.patch.object(
                orchestrator,
                "_preflight_for_run",
                return_value=fake_manager,
            ), mock.patch.object(
                orchestrator,
                "_run_requested_items",
                return_value="passed",
            ):
                result = orchestrator.run_new(
                    playbook_path="playbook.md",
                    item_id="01",
                    config_path=overlay_path.as_posix(),
                )

            saved = load_run_state(
                repo_root
                / ".local"
                / "automation"
                / "plan_orchestrator"
                / "runs"
                / "RUN_CFG_SNAPSHOT"
                / "run_state.json"
            )
            runtime_policy_path = repo_root / saved.runtime_policy_path
            payload = load_json(runtime_policy_path)

            self.assertEqual(result["run_id"], "RUN_CFG_SNAPSHOT")
            self.assertEqual(saved.options.codex_model, "overlay-codex")
            self.assertEqual(saved.options.max_fix_rounds, 5)
            self.assertEqual(saved.runtime_policy_sources["codex_model"], "config_file")
            self.assertEqual(saved.runtime_policy_sources["max_fix_rounds"], "config_file")
            self.assertEqual(
                saved.runtime_policy_path,
                ".local/automation/plan_orchestrator/runs/RUN_CFG_SNAPSHOT/runtime_policy.json",
            )
            self.assertEqual(saved.runtime_policy_sha256, compute_sha256(runtime_policy_path))
            self.assertEqual(payload["options"]["codex_model"], "overlay-codex")
            self.assertEqual(payload["sources"]["max_fix_rounds"], "config_file")
