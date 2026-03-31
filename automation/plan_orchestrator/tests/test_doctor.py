from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.plan_orchestrator.config import resolve_run_directories
from automation.plan_orchestrator.doctor import run_doctor
from automation.plan_orchestrator.reporting import write_playbook_snapshot
from automation.plan_orchestrator.state_store import create_run_state, save_run_state
from automation.plan_orchestrator.tests.support import (
    git_commit_all,
    init_git_repo,
    make_options,
    make_plan,
    write_minimal_playbook,
)
from automation.plan_orchestrator.validators import write_json_atomic


class DoctorTests(unittest.TestCase):
    def test_run_doctor_validates_preflight_and_playbook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            write_minimal_playbook(playbook_path)

            with mock.patch(
                "automation.plan_orchestrator.doctor.assert_clean_agent_environment",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.ensure_commands_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_git_identity_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_clean_tracked_checkout",
                return_value=None,
            ):
                report = run_doctor(repo_root, playbook_path=playbook_path)

        self.assertTrue(report["ok"])
        check_names = [entry["name"] for entry in report["checks"]]
        self.assertIn("agent_environment", check_names)
        self.assertIn("required_commands", check_names)
        self.assertIn("git_identity", check_names)
        self.assertIn("clean_tracked_checkout", check_names)
        self.assertIn("playbook_parse", check_names)
        self.assertIn("playbook_normalize", check_names)

    def test_run_doctor_can_validate_a_saved_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            write_minimal_playbook(playbook_path)
            init_git_repo(repo_root)
            baseline = git_commit_all(repo_root, "seed repo")
            subprocess_branch = "orchestrator/run/RUN_DOCTOR"
            subprocess.run(
                ["git", "branch", subprocess_branch, baseline],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            plan = make_plan()
            run_id = "RUN_DOCTOR"
            dirs = resolve_run_directories(repo_root, run_id)
            normalized_plan_path = dirs.run_root / "normalized_plan.json"
            write_json_atomic(normalized_plan_path, plan.to_dict())

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path=playbook_path.relative_to(repo_root).as_posix(),
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha=baseline,
                run_branch_name=subprocess_branch,
                options=make_options(),
                plan=plan,
            )
            save_run_state(dirs.run_state_path, run_state)

            with mock.patch(
                "automation.plan_orchestrator.doctor.assert_clean_agent_environment",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.ensure_commands_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_git_identity_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_clean_tracked_checkout",
                return_value=None,
            ):
                report = run_doctor(repo_root, run_id=run_id)

        self.assertTrue(report["ok"])
        check_names = [entry["name"] for entry in report["checks"]]
        self.assertIn("run_state_load", check_names)
        self.assertIn("run_references", check_names)

    def test_run_doctor_fix_safe_rebuilds_missing_normalized_plan_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            write_minimal_playbook(playbook_path)
            init_git_repo(repo_root)
            baseline = git_commit_all(repo_root, "seed repo")

            run_id = "RUN_DOCTOR_FIX"
            dirs = resolve_run_directories(repo_root, run_id)
            snapshot_path = dirs.run_root / "playbook_source_snapshot.md"
            write_playbook_snapshot(
                source_path=playbook_path,
                source_sha256="a" * 64,
                source_text=playbook_path.read_text(encoding="utf-8"),
                output_path=snapshot_path,
            )
            subprocess.run(
                ["git", "branch", f"orchestrator/run/{run_id}", baseline],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            plan = make_plan()
            normalized_plan_path = dirs.run_root / "normalized_plan.json"
            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path=playbook_path.relative_to(repo_root).as_posix(),
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha=baseline,
                run_branch_name=f"orchestrator/run/{run_id}",
                options=make_options(),
                plan=plan,
            )
            save_run_state(dirs.run_state_path, run_state)

            with mock.patch(
                "automation.plan_orchestrator.doctor.assert_clean_agent_environment",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.ensure_commands_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_git_identity_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_clean_tracked_checkout",
                return_value=None,
            ):
                report = run_doctor(repo_root, run_id=run_id, fix_safe=True)

            self.assertTrue(report["ok"])
            self.assertTrue(normalized_plan_path.exists())
            repairs = report["repairs"]
            self.assertEqual(repairs[0]["name"], "rebuild_normalized_plan")
            self.assertEqual(repairs[0]["status"], "applied")

    def test_run_doctor_reports_missing_refs_worktrees_and_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            write_minimal_playbook(playbook_path)
            init_git_repo(repo_root)
            baseline = git_commit_all(repo_root, "seed repo")

            run_id = "RUN_DOCTOR_REFS"
            dirs = resolve_run_directories(repo_root, run_id)
            normalized_plan_path = dirs.run_root / "normalized_plan.json"
            plan = make_plan()
            write_json_atomic(normalized_plan_path, plan.to_dict())
            subprocess.run(
                ["git", "branch", f"orchestrator/run/{run_id}", baseline],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path=playbook_path.relative_to(repo_root).as_posix(),
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha=baseline,
                run_branch_name=f"orchestrator/run/{run_id}",
                options=make_options(),
                plan=plan,
            )
            item_state = run_state.get_item_state("01")
            item_state.worktree_path = (
                dirs.worktrees_root / run_id / "item-01-attempt-1"
            ).relative_to(repo_root).as_posix()
            item_state.checkpoint_ref = "missing-checkpoint-ref"
            orphaned_dir = dirs.worktrees_root / run_id / "item-99-attempt-1"
            orphaned_dir.mkdir(parents=True, exist_ok=True)
            save_run_state(dirs.run_state_path, run_state)

            with mock.patch(
                "automation.plan_orchestrator.doctor.assert_clean_agent_environment",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.ensure_commands_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_git_identity_available",
                return_value=None,
            ), mock.patch(
                "automation.plan_orchestrator.doctor.WorktreeManager.assert_clean_tracked_checkout",
                return_value=None,
            ):
                report = run_doctor(repo_root, run_id=run_id)

        self.assertFalse(report["ok"])
        run_refs = next(entry for entry in report["checks"] if entry["name"] == "run_references")
        self.assertEqual(run_refs["status"], "error")
        self.assertIn(item_state.worktree_path, run_refs["missing_worktrees"])
        self.assertIn("missing-checkpoint-ref", run_refs["missing_checkpoint_refs"])
        self.assertIn(orphaned_dir.relative_to(repo_root).as_posix(), run_refs["orphaned_worktrees"])
