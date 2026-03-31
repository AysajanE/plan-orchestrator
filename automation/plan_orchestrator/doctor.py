from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import build_default_adapter
from .config import WORKTREES_ROOT, assert_clean_agent_environment, resolve_run_directories
from .playbook_parser import parse_playbook
from .state_store import load_run_state
from .validators import resolve_repo_path
from .worktree_manager import WorktreeManager


def run_doctor(
    repo_root: Path,
    *,
    playbook_path: str | Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    manager = WorktreeManager(repo_root, repo_root / WORKTREES_ROOT)

    ok &= _capture_check(checks, "agent_environment", lambda: assert_clean_agent_environment(repo_root))
    ok &= _capture_check(checks, "required_commands", manager.ensure_commands_available)
    ok &= _capture_check(checks, "git_identity", manager.assert_git_identity_available)
    ok &= _capture_check(checks, "clean_tracked_checkout", manager.assert_clean_tracked_checkout)

    if playbook_path is not None:
        playbook = resolve_repo_path(repo_root, playbook_path)
        parsed: dict[str, Any] | None = None
        parsed_ok, parsed = _capture_result_check(
            checks,
            "playbook_parse",
            lambda: parse_playbook(playbook),
            on_ok=lambda result: {
                "path": playbook.relative_to(repo_root).as_posix()
                if playbook.is_relative_to(repo_root)
                else playbook.as_posix(),
                "sha256": result["sha256"],
            },
        )
        ok &= parsed_ok
        if parsed_ok and parsed is not None:
            adapter = build_default_adapter(repo_root)
            normalized_ok, plan = _capture_result_check(
                checks,
                "playbook_normalize",
                lambda: adapter.normalize(parsed, playbook),
                on_ok=lambda result: {
                    "adapter_id": result.adapter_id,
                    "item_count": len(result.items),
                },
            )
            ok &= normalized_ok
            _ = plan

    if run_id is not None:
        dirs = resolve_run_directories(repo_root, run_id)
        run_state_ok, run_state = _capture_result_check(
            checks,
            "run_state_load",
            lambda: load_run_state(dirs.run_state_path),
            on_ok=lambda result: {
                "run_id": result.run_id,
                "current_state": result.current_state,
                "current_item_id": result.current_item_id,
            },
        )
        ok &= run_state_ok
        if run_state_ok and run_state is not None:
            references = {
                "playbook_source_path_exists": resolve_repo_path(
                    repo_root, run_state.playbook_source_path
                ).exists(),
                "normalized_plan_path_exists": resolve_repo_path(
                    repo_root, run_state.normalized_plan_path
                ).exists(),
            }
            status = "ok" if all(references.values()) else "error"
            checks.append(
                {
                    "name": "run_references",
                    "status": status,
                    "run_id": run_state.run_id,
                    "checks": references,
                }
            )
            ok &= status == "ok"

    return {
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "repo_root": repo_root.as_posix(),
        "checks": checks,
    }


def _capture_check(
    checks: list[dict[str, Any]],
    name: str,
    func,
) -> bool:
    success, _ = _capture_result_check(checks, name, func)
    return success


def _capture_result_check(
    checks: list[dict[str, Any]],
    name: str,
    func,
    *,
    on_ok=None,
) -> tuple[bool, Any]:
    try:
        result = func()
    except Exception as exc:
        checks.append(
            {
                "name": name,
                "status": "error",
                "detail": str(exc),
            }
        )
        return False, None

    entry = {"name": name, "status": "ok"}
    if on_ok is not None:
        entry.update(on_ok(result))
    checks.append(entry)
    return True, result
