from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.plan_orchestrator.worktree_manager import GitError, WorktreeManager


def init_git_repo(repo_root: Path, *, configure_identity: bool = True) -> None:
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    if configure_identity:
        subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo_root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo_root, check=True)


def git_commit_all(repo_root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True).stdout.strip()


class WorktreeManagerTests(unittest.TestCase):
    def test_assert_git_identity_available_rejects_missing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root, configure_identity=False)
            manager = WorktreeManager(repo_root, repo_root / ".local" / "worktrees")

            real_git = manager._git

            def fake_git(*args: str, **kwargs):
                if args in {("config", "--get", "user.name"), ("config", "--get", "user.email")}:
                    return subprocess.CompletedProcess(["git", *args], 1, "", "")
                return real_git(*args, **kwargs)

            with mock.patch.object(manager, "_git", side_effect=fake_git):
                with self.assertRaisesRegex(GitError, "user.name"):
                    manager.assert_git_identity_available()

    def test_assert_clean_tracked_checkout_rejects_dirty_tracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            tracked = repo_root / "README.md"
            tracked.write_text("baseline\n", encoding="utf-8")
            git_commit_all(repo_root, "initial")
            tracked.write_text("dirty\n", encoding="utf-8")

            manager = WorktreeManager(repo_root, repo_root / ".local" / "worktrees")

            with self.assertRaises(GitError):
                manager.assert_clean_tracked_checkout()

    def test_ensure_commands_available_rejects_missing_agent_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            manager = WorktreeManager(repo_root, repo_root / ".local" / "worktrees")

            for missing_command in ("codex", "claude"):
                def fake_which(command: str) -> str | None:
                    if command == missing_command:
                        return None
                    return f"/usr/bin/{command}"

                with self.subTest(missing_command=missing_command):
                    with mock.patch(
                        "automation.plan_orchestrator.worktree_manager.shutil.which",
                        side_effect=fake_which,
                    ):
                        with self.assertRaisesRegex(GitError, missing_command):
                            manager.ensure_commands_available()

    def test_prepare_item_worktree_and_fast_forward_run_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            tracked = repo_root / "docs" / "runbooks" / "demo.md"
            tracked.parent.mkdir(parents=True, exist_ok=True)
            tracked.write_text("baseline\n", encoding="utf-8")
            base_head = git_commit_all(repo_root, "initial")

            manager = WorktreeManager(repo_root, repo_root / ".local" / "worktrees")
            run_branch = manager.ensure_run_branch("RUN_TEST", base_head)
            metadata = manager.prepare_item_worktree(
                run_id="RUN_TEST",
                item_id="01",
                attempt_number=1,
                run_branch_name=run_branch,
            )

            worktree_path = repo_root / metadata.path
            self.assertTrue(worktree_path.exists())
            self.assertTrue((repo_root / metadata.workspace_packet_root).exists())

            changed = worktree_path / "docs" / "runbooks" / "demo.md"
            changed.write_text("updated\n", encoding="utf-8")
            git_commit_all(worktree_path, "item change")
            item_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            new_run_head = manager.fast_forward_run_branch(run_branch, metadata.branch_name)

            self.assertEqual(new_run_head, item_head)
            resolved_run_head = subprocess.run(
                ["git", "rev-parse", run_branch],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(resolved_run_head, item_head)

    def test_fast_forward_run_branch_to_explicit_checkpoint_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            tracked = repo_root / "docs" / "runbooks" / "demo.md"
            tracked.parent.mkdir(parents=True, exist_ok=True)
            tracked.write_text("baseline\n", encoding="utf-8")
            base_head = git_commit_all(repo_root, "initial")

            manager = WorktreeManager(repo_root, repo_root / ".local" / "worktrees")
            run_branch = manager.ensure_run_branch("RUN_TEST", base_head)
            metadata = manager.prepare_item_worktree(
                run_id="RUN_TEST",
                item_id="01",
                attempt_number=1,
                run_branch_name=run_branch,
            )

            worktree_path = repo_root / metadata.path
            changed = worktree_path / "docs" / "runbooks" / "demo.md"
            changed.write_text("checkpoint\n", encoding="utf-8")
            checkpoint_ref = git_commit_all(worktree_path, "checkpoint")
            changed.write_text("later\n", encoding="utf-8")
            later_ref = git_commit_all(worktree_path, "later change")

            updated_run_head = manager.fast_forward_run_branch_to_ref(run_branch, checkpoint_ref)

            self.assertEqual(updated_run_head, checkpoint_ref)
            resolved_run_head = subprocess.run(
                ["git", "rev-parse", run_branch],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(resolved_run_head, checkpoint_ref)
            self.assertNotEqual(resolved_run_head, later_ref)

    def test_create_run_refresh_branch_requires_descendant_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            tracked = repo_root / "docs" / "runbooks" / "demo.md"
            tracked.parent.mkdir(parents=True, exist_ok=True)
            tracked.write_text("baseline\n", encoding="utf-8")
            initial = git_commit_all(repo_root, "initial")

            manager = WorktreeManager(repo_root, repo_root / ".local" / "worktrees")
            run_branch = manager.ensure_run_branch("RUN_TEST", initial)

            tracked.write_text("descendant\n", encoding="utf-8")
            descendant = git_commit_all(repo_root, "descendant")

            refresh_branch = manager.create_run_refresh_branch(
                run_id="RUN_TEST",
                current_run_branch_name=run_branch,
                target_ref=descendant,
            )
            self.assertEqual(refresh_branch, "orchestrator/run-refresh/RUN_TEST/1")
            self.assertEqual(manager.resolve_ref(refresh_branch), descendant)

            manager._git("checkout", initial)
            manager._git("checkout", "-b", "side-branch")
            tracked.write_text("sibling\n", encoding="utf-8")
            sibling = git_commit_all(repo_root, "sibling")

            with self.assertRaisesRegex(GitError, "cannot be refreshed"):
                manager.create_run_refresh_branch(
                    run_id="RUN_TEST",
                    current_run_branch_name=refresh_branch,
                    target_ref=sibling,
                )
