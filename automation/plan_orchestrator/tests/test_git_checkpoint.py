import subprocess
import tempfile
import unittest
from pathlib import Path

from automation.plan_orchestrator.git_checkpoint import (
    ScopeViolation,
    collect_post_checkpoint_authority_violations,
    collect_forbidden_paths,
    stage_allowed_changes,
)


def init_git_repo(repo_root: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo_root, check=True)


def git_commit_all(repo_root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True).stdout.strip()


def git_force_commit_path(repo_root: Path, rel_path: str, *, message: str) -> str:
    subprocess.run(["git", "add", "-f", "--", rel_path], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True).stdout.strip()


class GitCheckpointTests(unittest.TestCase):
    def test_collect_forbidden_paths_matches_sensitive_roots_including_git_metadata(self) -> None:
        self.assertEqual(
            collect_forbidden_paths(
                [
                    ".git/config",
                    ".local/plan_orchestrator/control.json",
                    ".codex/settings.toml",
                    ".claude/settings.json",
                    ".mcp.json",
                    "ops/config/policy.toml",
                    "secrets/token.txt",
                    "docs/runbooks/demo.md",
                ]
            ),
            [
                ".git/config",
                ".local/plan_orchestrator/control.json",
                ".codex/settings.toml",
                ".claude/settings.json",
                ".mcp.json",
                "ops/config/policy.toml",
                "secrets/token.txt",
            ],
        )

    def test_stage_allowed_changes_rejects_forbidden_roots(self) -> None:
        cases = [
            (".local/plan_orchestrator/control.json", True),
            (".codex/settings.toml", False),
            (".claude/settings.json", False),
            (".mcp.json", False),
            ("ops/config/policy.toml", False),
            ("secrets/token.txt", False),
        ]

        for rel_path, tracked_baseline in cases:
            with self.subTest(path=rel_path):
                with tempfile.TemporaryDirectory() as tmp:
                    repo_root = Path(tmp)
                    init_git_repo(repo_root)
                    readme = repo_root / "README.md"
                    readme.write_text("baseline\n", encoding="utf-8")
                    git_commit_all(repo_root, "initial")

                    target = repo_root / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("baseline\n", encoding="utf-8")
                    if tracked_baseline:
                        git_force_commit_path(repo_root, rel_path, message="track forbidden control file")
                        target.write_text("dirty\n", encoding="utf-8")

                    with self.assertRaises(ScopeViolation) as exc_info:
                        stage_allowed_changes(
                            worktree_path=repo_root,
                            allowed_write_roots=["docs/runbooks"],
                        )

                    self.assertIn("Forbidden repo changes detected", str(exc_info.exception))
                    self.assertIn(rel_path, str(exc_info.exception))

    def test_stage_allowed_changes_keeps_explicit_cache_roots_ignored(self) -> None:
        for rel_path in ("out/build.log", "cache/tool/state.json", "venv/bin/activate", "node_modules/pkg/index.js"):
            with self.subTest(path=rel_path):
                with tempfile.TemporaryDirectory() as tmp:
                    repo_root = Path(tmp)
                    init_git_repo(repo_root)
                    readme = repo_root / "README.md"
                    readme.write_text("baseline\n", encoding="utf-8")
                    git_commit_all(repo_root, "initial")

                    target = repo_root / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("cache\n", encoding="utf-8")

                    staged = stage_allowed_changes(
                        worktree_path=repo_root,
                        allowed_write_roots=["docs/runbooks"],
                    )

                    self.assertEqual(staged, [])

    def test_collect_post_checkpoint_authority_violations_reports_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            readme = repo_root / "README.md"
            readme.write_text("baseline\n", encoding="utf-8")
            checkpoint_ref = git_commit_all(repo_root, "initial")

            untracked = repo_root / "scratch.txt"
            untracked.write_text("post verification drift\n", encoding="utf-8")

            violations = collect_post_checkpoint_authority_violations(
                worktree_path=repo_root,
                checkpoint_ref=checkpoint_ref,
            )

            self.assertEqual(
                violations,
                ["Verification dirtied files after checkpoint: scratch.txt"],
            )
