from __future__ import annotations

import subprocess
from pathlib import Path

from .validators import dedupe_preserve_order, out_of_scope_paths, relative_path_is_within


class ScopeViolation(RuntimeError):
    pass


FORBIDDEN_ROOTS = [
    ".local",
    ".git",
    ".codex",
    ".claude",
    ".mcp.json",
    "ops/config",
    "secrets",
]

IGNORED_CACHE_ROOTS = [
    "out",
    "cache",
    "venv",
    "node_modules",
]


def _git(
    worktree_path: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(worktree_path),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit code {result.returncode}: {result.stderr.strip()}"
        )
    return result


def _parse_status_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for raw_line in status_output.splitlines():
        if not raw_line:
            continue
        path_text = raw_line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        paths.append(path_text)
    return dedupe_preserve_order(paths)


def collect_dirty_paths(worktree_path: Path) -> list[str]:
    status = _git(worktree_path, "status", "--porcelain=v1", "--untracked-files=all").stdout
    return _parse_status_paths(status)


def collect_tracked_dirty_paths(worktree_path: Path) -> list[str]:
    status = _git(worktree_path, "status", "--porcelain=v1", "--untracked-files=no").stdout
    return _parse_status_paths(status)


def _normalize_repo_paths(paths: list[str]) -> list[str]:
    return dedupe_preserve_order([path.strip("/") for path in paths if path.strip("/")])


def _paths_within_roots(paths: list[str], roots: list[str]) -> list[str]:
    normalized_paths = _normalize_repo_paths(paths)
    normalized_roots = [root.strip("/") for root in roots if root.strip("/")]
    return [
        rel_path
        for rel_path in normalized_paths
        if any(relative_path_is_within(rel_path, root) for root in normalized_roots)
    ]


def collect_forbidden_paths(paths: list[str]) -> list[str]:
    return _paths_within_roots(paths, FORBIDDEN_ROOTS)


def classify_scope_paths(
    *,
    paths: list[str],
    allowed_write_roots: list[str],
) -> tuple[list[str], list[str]]:
    normalized_paths = _normalize_repo_paths(paths)
    forbidden_paths = collect_forbidden_paths(normalized_paths)
    scope_violations = out_of_scope_paths(
        normalized_paths,
        allowed_write_roots,
        ignored_roots=IGNORED_CACHE_ROOTS,
    )
    return forbidden_paths, scope_violations


def read_head_ref(worktree_path: Path) -> str:
    return _git(worktree_path, "rev-parse", "HEAD").stdout.strip()


def collect_post_checkpoint_authority_violations(
    *,
    worktree_path: Path,
    checkpoint_ref: str,
) -> list[str]:
    violations: list[str] = []

    current_head = read_head_ref(worktree_path)
    if current_head != checkpoint_ref:
        drift_paths = changed_paths_between(
            worktree_path=worktree_path,
            base_ref=checkpoint_ref,
            target_ref=current_head,
        )
        detail = f": {', '.join(drift_paths)}" if drift_paths else ""
        violations.append(
            f"Verification moved HEAD away from checkpoint {checkpoint_ref} to {current_head}{detail}"
        )

    dirty_paths = collect_dirty_paths(worktree_path)
    if dirty_paths:
        violations.append(
            "Verification dirtied files after checkpoint: "
            + ", ".join(dirty_paths)
        )

    return violations


def validate_scope_for_dirty_paths(
    *,
    dirty_paths: list[str],
    allowed_write_roots: list[str],
) -> list[str]:
    forbidden_paths, scope_violations = classify_scope_paths(
        paths=dirty_paths,
        allowed_write_roots=allowed_write_roots,
    )
    return dedupe_preserve_order(forbidden_paths + scope_violations)


def stage_allowed_changes(
    *,
    worktree_path: Path,
    allowed_write_roots: list[str],
) -> list[str]:
    dirty_paths = _normalize_repo_paths(collect_dirty_paths(worktree_path))
    forbidden_paths, scope_violations = classify_scope_paths(
        paths=dirty_paths,
        allowed_write_roots=allowed_write_roots,
    )
    if forbidden_paths:
        raise ScopeViolation(
            "Forbidden repo changes detected: " + ", ".join(forbidden_paths)
        )
    if scope_violations:
        raise ScopeViolation(
            "Out-of-scope repo changes detected: " + ", ".join(scope_violations)
        )

    stageable = [
        path for path in dirty_paths
        if not any(relative_path_is_within(path, root) for root in IGNORED_CACHE_ROOTS)
    ]
    if not stageable:
        return []

    for path in stageable:
        _git(worktree_path, "add", "-A", "--", path)

    return stageable


def staged_changes_exist(worktree_path: Path) -> bool:
    result = _git(worktree_path, "diff", "--cached", "--quiet", check=False)
    return result.returncode != 0


def create_checkpoint_commit(
    *,
    worktree_path: Path,
    item_id: str,
    stage_name: str,
) -> str:
    if staged_changes_exist(worktree_path):
        message = f"orchestrator({item_id}): {stage_name} checkpoint"
        _git(worktree_path, "commit", "-m", message)
    return _git(worktree_path, "rev-parse", "HEAD").stdout.strip()


def changed_paths_between(
    *,
    worktree_path: Path,
    base_ref: str,
    target_ref: str = "HEAD",
) -> list[str]:
    output = _git(
        worktree_path,
        "diff",
        "--name-only",
        f"{base_ref}..{target_ref}",
    ).stdout
    return [line.strip() for line in output.splitlines() if line.strip()]


def scope_check_for_committed_changes(
    *,
    worktree_path: Path,
    base_ref: str,
    target_ref: str,
    allowed_write_roots: list[str],
) -> tuple[str, list[str]]:
    changed = changed_paths_between(
        worktree_path=worktree_path,
        base_ref=base_ref,
        target_ref=target_ref,
    )
    forbidden_paths, out_of_scope = classify_scope_paths(
        paths=changed,
        allowed_write_roots=allowed_write_roots,
    )
    violations = dedupe_preserve_order(forbidden_paths + out_of_scope)
    note = "All committed changes are within allowed write roots."
    if forbidden_paths:
        note = "Committed changes include forbidden paths."
    elif violations:
        note = "Committed changes include out-of-scope paths."
    return note, violations


def generate_patch(
    *,
    worktree_path: Path,
    base_ref: str,
    target_ref: str,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    diff = _git(
        worktree_path,
        "diff",
        "--binary",
        f"{base_ref}..{target_ref}",
    ).stdout
    output_path.write_text(diff, encoding="utf-8")
