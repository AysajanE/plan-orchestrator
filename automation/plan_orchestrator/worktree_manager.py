from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .models import WorktreeMetadata
from .validators import ensure_directory, repo_relative_path


class GitError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, repo_root: Path, worktrees_root: Path) -> None:
        self.repo_root = repo_root
        self.worktrees_root = worktrees_root

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.repo_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed with exit code {result.returncode}: {result.stderr.strip()}"
            )
        return result

    def assert_clean_tracked_checkout(self) -> None:
        status = self._git("status", "--porcelain=v1", "--untracked-files=no").stdout.strip()
        if status:
            raise GitError(
                "Tracked main checkout must be clean before starting the orchestrator.\n"
                f"{status}"
            )

    def assert_git_identity_available(self) -> None:
        missing = []
        for key in ("user.name", "user.email"):
            result = self._git("config", "--get", key, check=False)
            if result.returncode != 0 or not result.stdout.strip():
                missing.append(f"git config {key}")
        if missing:
            raise GitError(
                "Git identity required for checkpoint commits is not configured: "
                + ", ".join(missing)
            )

    def ensure_commands_available(self) -> None:
        for command in ("git", "codex", "claude"):
            if shutil.which(command) is None:
                raise GitError(f"Required command is not available: {command}")

    def current_head_sha(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def resolve_ref(self, ref: str) -> str:
        return self._git("rev-parse", ref).stdout.strip()

    def branch_exists(self, branch_name: str) -> bool:
        result = self._git("show-ref", "--verify", f"refs/heads/{branch_name}", check=False)
        return result.returncode == 0

    def ensure_run_branch(self, run_id: str, base_head_sha: str) -> str:
        branch_name = f"orchestrator/run/{run_id}"
        if not self.branch_exists(branch_name):
            self._git("branch", branch_name, base_head_sha)
        return branch_name

    def prepare_item_worktree(
        self,
        *,
        run_id: str,
        item_id: str,
        attempt_number: int,
        run_branch_name: str,
    ) -> WorktreeMetadata:
        item_branch = f"orchestrator/item/{run_id}/{item_id}/attempt-{attempt_number}"
        worktree_path = self.worktrees_root / run_id / f"item-{item_id}-attempt-{attempt_number}"
        packet_root = worktree_path / ".local" / "plan_orchestrator" / "packet"

        ensure_directory(worktree_path.parent)

        if worktree_path.exists():
            raise GitError(
                f"Refusing to reuse existing worktree path automatically: {worktree_path}"
            )

        if self.branch_exists(item_branch):
            self._git("worktree", "add", str(worktree_path), item_branch)
        else:
            self._git("worktree", "add", "-b", item_branch, str(worktree_path), run_branch_name)

        head_ref = self._git("rev-parse", "HEAD", cwd=worktree_path).stdout.strip()
        ensure_directory(packet_root)

        return WorktreeMetadata(
            path=repo_relative_path(self.repo_root, worktree_path),
            branch_name=item_branch,
            base_ref=run_branch_name,
            head_ref=head_ref,
            workspace_packet_root=repo_relative_path(self.repo_root, packet_root),
        )

    def read_head_ref(self, worktree_path: Path) -> str:
        return self._git("rev-parse", "HEAD", cwd=worktree_path).stdout.strip()

    def fast_forward_run_branch_to_ref(self, run_branch_name: str, target_ref: str) -> str:
        run_sha = self._git("rev-parse", run_branch_name).stdout.strip()
        target_sha = self._git("rev-parse", target_ref).stdout.strip()

        ancestor_check = self._git(
            "merge-base",
            "--is-ancestor",
            run_sha,
            target_sha,
            check=False,
        )
        if ancestor_check.returncode != 0:
            raise GitError(
                f"Run branch {run_branch_name} cannot be fast-forwarded to {target_ref}"
            )

        self._git("update-ref", f"refs/heads/{run_branch_name}", target_sha, run_sha)
        return target_sha

    def fast_forward_run_branch(self, run_branch_name: str, item_branch_name: str) -> str:
        return self.fast_forward_run_branch_to_ref(run_branch_name, item_branch_name)

    def create_run_refresh_branch(
        self,
        *,
        run_id: str,
        current_run_branch_name: str,
        target_ref: str,
    ) -> str:
        current_run_sha = self.resolve_ref(current_run_branch_name)
        target_sha = self.resolve_ref(target_ref)

        ancestor_check = self._git(
            "merge-base",
            "--is-ancestor",
            current_run_sha,
            target_sha,
            check=False,
        )
        if ancestor_check.returncode != 0:
            raise GitError(
                f"Run branch {current_run_branch_name} cannot be refreshed to non-descendant ref {target_ref}"
            )

        prefix = f"orchestrator/run-refresh/{run_id}/"
        index = 1
        while self.branch_exists(f"{prefix}{index}"):
            index += 1

        branch_name = f"{prefix}{index}"
        self._git("branch", branch_name, target_sha)
        return branch_name
