from __future__ import annotations

import subprocess
import tempfile
import unittest
from contextlib import contextmanager
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


@contextmanager
def patched_doctor_preflight():
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
        yield


class RepairTests(unittest.TestCase):
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

            with patched_doctor_preflight():
                report = run_doctor(repo_root, run_id=run_id, fix_safe=True)

            self.assertTrue(report["ok"])
            self.assertTrue(normalized_plan_path.exists())
            repairs = report["repairs"]
            self.assertEqual(repairs[0]["name"], "rebuild_normalized_plan")
            self.assertEqual(repairs[0]["status"], "applied")

    def test_run_doctor_fix_safe_skips_when_snapshot_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            write_minimal_playbook(playbook_path)
            init_git_repo(repo_root)
            baseline = git_commit_all(repo_root, "seed repo")

            run_id = "RUN_DOCTOR_FIX_NO_SNAPSHOT"
            dirs = resolve_run_directories(repo_root, run_id)
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

            with patched_doctor_preflight():
                report = run_doctor(repo_root, run_id=run_id, fix_safe=True)

            self.assertFalse(normalized_plan_path.exists())
            self.assertEqual(report["repairs"][0]["status"], "skipped")
            self.assertIn("Missing playbook snapshot", report["repairs"][0]["detail"])

    def test_run_doctor_fix_safe_refuses_to_rewrite_out_of_scope_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            write_minimal_playbook(playbook_path)
            readme_path = repo_root / "README.md"
            readme_path.write_text("keep this tracked content\n", encoding="utf-8")
            init_git_repo(repo_root)
            baseline = git_commit_all(repo_root, "seed repo")

            run_id = "RUN_DOCTOR_FIX_BOUNDARY"
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
            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path=playbook_path.relative_to(repo_root).as_posix(),
                playbook_source_sha256="a" * 64,
                normalized_plan_path="README.md",
                base_head_sha=baseline,
                run_branch_name=f"orchestrator/run/{run_id}",
                options=make_options(),
                plan=plan,
            )
            save_run_state(dirs.run_state_path, run_state)

            with patched_doctor_preflight():
                report = run_doctor(repo_root, run_id=run_id, fix_safe=True)

            self.assertFalse(report["ok"])
            self.assertEqual(readme_path.read_text(encoding="utf-8"), "keep this tracked content\n")
            run_refs = next(entry for entry in report["checks"] if entry["name"] == "run_references")
            self.assertFalse(run_refs["checks"]["normalized_plan_path_within_run_root"])
            self.assertEqual(report["repairs"][0]["status"], "skipped")
            self.assertIn("Refusing to rewrite out-of-scope normalized_plan_path", report["repairs"][0]["detail"])
