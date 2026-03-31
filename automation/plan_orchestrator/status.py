from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import RUNS_ROOT, resolve_run_directories
from .models import ItemRunState, RunState
from .state_store import load_run_state
from .validators import resolve_repo_path


WAITING_TERMINALS = {"awaiting_human_gate", "blocked_external"}
ERROR_TERMINALS = {"escalated"}
KNOWN_TERMINALS = ("none", "passed", "awaiting_human_gate", "blocked_external", "escalated")


def load_run_status(repo_root: Path, run_id: str) -> dict[str, Any]:
    dirs = resolve_run_directories(repo_root, run_id)
    run_state_path = dirs.run_state_path
    if not run_state_path.exists():
        return _broken_run_status(
            run_id=run_id,
            run_state_path=run_state_path,
            error=f"Run state not found: {run_state_path.relative_to(repo_root).as_posix()}",
        )

    try:
        run_state = load_run_state(run_state_path)
    except Exception as exc:
        return _broken_run_status(
            run_id=run_id,
            run_state_path=run_state_path,
            error=str(exc),
        )

    try:
        return _build_run_status(repo_root=repo_root, run_state=run_state, run_state_path=run_state_path)
    except Exception as exc:
        return _broken_run_status(
            run_id=run_id,
            run_state_path=run_state_path,
            error=str(exc),
        )


def list_run_statuses(repo_root: Path) -> list[dict[str, Any]]:
    runs_root = repo_root / RUNS_ROOT
    if not runs_root.exists():
        return []

    summaries = [
        load_run_status(repo_root, run_dir.name)
        for run_dir in runs_root.iterdir()
        if run_dir.is_dir()
    ]
    return sorted(
        summaries,
        key=lambda summary: (
            str(summary.get("updated_at_utc") or ""),
            str(summary.get("run_id") or ""),
        ),
        reverse=True,
    )


def _build_run_status(repo_root: Path, run_state: RunState, run_state_path: Path) -> dict[str, Any]:
    focus_item = _focus_item(run_state)
    current_item = _item_summary(focus_item) if focus_item is not None else None
    checks = _path_checks(repo_root=repo_root, run_state=run_state, run_state_path=run_state_path, item_state=focus_item)
    pending_action = _pending_action(focus_item=focus_item, checks=checks)
    status_level, exit_code = _status_health(run_state=run_state, checks=checks)

    return {
        "run_id": run_state.run_id,
        "current_state": run_state.current_state,
        "current_item_id": run_state.current_item_id,
        "run_branch_name": run_state.run_branch_name,
        "playbook_source_path": run_state.playbook_source_path,
        "normalized_plan_path": run_state.normalized_plan_path,
        "updated_at_utc": run_state.updated_at_utc,
        "status_level": status_level,
        "exit_code": exit_code,
        "pending_action": pending_action,
        "current_item": current_item,
        "terminal_counts": _terminal_counts(run_state),
        "checks": checks,
    }


def _broken_run_status(*, run_id: str, run_state_path: Path, error: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "current_state": None,
        "current_item_id": None,
        "run_branch_name": None,
        "playbook_source_path": None,
        "normalized_plan_path": None,
        "updated_at_utc": None,
        "status_level": "error",
        "exit_code": 2,
        "pending_action": {
            "kind": "repair_local_state",
            "detail": error,
        },
        "current_item": None,
        "terminal_counts": {name: 0 for name in KNOWN_TERMINALS},
        "checks": {
            "run_state_path_exists": run_state_path.exists(),
            "playbook_source_path_exists": None,
            "normalized_plan_path_exists": None,
            "current_item_worktree_exists": None,
            "manual_gate_path_exists": None,
            "escalation_manifest_path_exists": None,
        },
        "error": error,
    }


def _focus_item(run_state: RunState) -> ItemRunState | None:
    if run_state.current_item_id:
        return run_state.get_item_state(run_state.current_item_id)

    for item_state in sorted(run_state.items, key=lambda value: value.order):
        if item_state.terminal_state in ERROR_TERMINALS | WAITING_TERMINALS:
            return item_state

    for item_state in sorted(run_state.items, key=lambda value: value.order):
        if item_state.terminal_state != "passed":
            return item_state

    return None


def _item_summary(item_state: ItemRunState) -> dict[str, Any]:
    return {
        "item_id": item_state.item_id,
        "state": item_state.state,
        "attempt_number": item_state.attempt_number,
        "terminal_state": item_state.terminal_state,
        "manual_gate_status": item_state.manual_gate_status,
        "external_check_status": item_state.external_check_status,
        "branch_name": item_state.branch_name,
        "worktree_path": item_state.worktree_path,
        "checkpoint_ref": item_state.checkpoint_ref,
        "latest_paths": item_state.latest_paths.to_dict(),
        "updated_at_utc": item_state.updated_at_utc,
    }


def _path_checks(
    *,
    repo_root: Path,
    run_state: RunState,
    run_state_path: Path,
    item_state: ItemRunState | None,
) -> dict[str, bool | None]:
    checks: dict[str, bool | None] = {
        "run_state_path_exists": run_state_path.exists(),
        "playbook_source_path_exists": resolve_repo_path(repo_root, run_state.playbook_source_path).exists(),
        "normalized_plan_path_exists": resolve_repo_path(
            repo_root, run_state.normalized_plan_path
        ).exists(),
        "current_item_worktree_exists": None,
        "manual_gate_path_exists": None,
        "escalation_manifest_path_exists": None,
    }

    if item_state is None:
        return checks

    if item_state.worktree_path:
        checks["current_item_worktree_exists"] = resolve_repo_path(
            repo_root, item_state.worktree_path
        ).exists()
    if item_state.latest_paths.manual_gate_path:
        checks["manual_gate_path_exists"] = resolve_repo_path(
            repo_root, item_state.latest_paths.manual_gate_path
        ).exists()
    if item_state.latest_paths.escalation_manifest_path:
        checks["escalation_manifest_path_exists"] = resolve_repo_path(
            repo_root, item_state.latest_paths.escalation_manifest_path
        ).exists()
    return checks


def _pending_action(
    *,
    focus_item: ItemRunState | None,
    checks: dict[str, bool | None],
) -> dict[str, Any] | None:
    if focus_item is None:
        if any(value is False for value in checks.values() if value is not None):
            return {
                "kind": "repair_local_state",
                "detail": "Referenced local artifacts are missing.",
            }
        return None

    if focus_item.terminal_state == "awaiting_human_gate":
        return {
            "kind": "manual_gate",
            "item_id": focus_item.item_id,
            "path": focus_item.latest_paths.manual_gate_path,
            "detail": "Manual gate decision required.",
        }
    if focus_item.terminal_state == "blocked_external":
        return {
            "kind": "external_evidence",
            "item_id": focus_item.item_id,
            "detail": "Human-supplied external evidence is still required.",
        }
    if focus_item.terminal_state == "escalated":
        return {
            "kind": "escalated",
            "item_id": focus_item.item_id,
            "path": focus_item.latest_paths.escalation_manifest_path,
            "detail": "Operator review is required before continuing.",
        }
    if any(value is False for value in checks.values() if value is not None):
        return {
            "kind": "repair_local_state",
            "item_id": focus_item.item_id,
            "detail": "Referenced local artifacts are missing.",
        }
    return None


def _status_health(
    *,
    run_state: RunState,
    checks: dict[str, bool | None],
) -> tuple[str, int]:
    if any(value is False for value in checks.values() if value is not None):
        return "error", 2

    terminal_states = {item.terminal_state for item in run_state.items}
    if terminal_states & ERROR_TERMINALS:
        return "error", 2
    if terminal_states & WAITING_TERMINALS:
        return "waiting", 1
    return "ok", 0


def _terminal_counts(run_state: RunState) -> dict[str, int]:
    counts = {name: 0 for name in KNOWN_TERMINALS}
    for item_state in run_state.items:
        key = item_state.terminal_state if item_state.terminal_state in counts else "none"
        counts[key] += 1
    return counts
