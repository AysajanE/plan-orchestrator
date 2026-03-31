from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .adapters import build_default_adapter
from .config import WORKTREES_ROOT, assert_clean_agent_environment, resolve_run_directories
from .models import RunState
from .playbook_parser import parse_playbook
from .runtime_policy import RUNTIME_POLICY_CHECK_KEYS, runtime_policy_integrity
from .state_store import load_run_state
from .validators import load_json, resolve_repo_path, validate_named_schema, write_json_atomic
from .worktree_manager import WorktreeManager


def run_doctor(
    repo_root: Path,
    *,
    playbook_path: str | Path | None = None,
    run_id: str | None = None,
    fix_safe: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
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
            reference_report = _evaluate_run_references(repo_root, manager, run_state)
            if fix_safe:
                repairs.extend(_apply_safe_repairs(repo_root, run_state, reference_report))
                reference_report = _evaluate_run_references(repo_root, manager, run_state)
            checks.append(reference_report)
            ok &= reference_report["status"] != "error"

    return {
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "repo_root": repo_root.as_posix(),
        "checks": checks,
        "repairs": repairs,
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


def _evaluate_run_references(
    repo_root: Path,
    manager: WorktreeManager,
    run_state: RunState,
) -> dict[str, Any]:
    playbook_path = resolve_repo_path(repo_root, run_state.playbook_source_path)
    normalized_plan_path = resolve_repo_path(repo_root, run_state.normalized_plan_path)
    snapshot_path = resolve_run_directories(repo_root, run_state.run_id).run_root / "playbook_source_snapshot.md"

    normalized_plan_valid = False
    normalized_plan_error: str | None = None
    if normalized_plan_path.exists():
        try:
            validate_named_schema("normalized_plan.schema.json", load_json(normalized_plan_path))
            normalized_plan_valid = True
        except Exception as exc:
            normalized_plan_error = str(exc)

    missing_item_branches: list[str] = []
    missing_checkpoint_refs: list[str] = []
    missing_worktrees: list[str] = []
    referenced_worktrees: set[str] = set()
    for item_state in run_state.items:
        if item_state.branch_name and not manager.branch_exists(item_state.branch_name):
            missing_item_branches.append(item_state.branch_name)
        if item_state.checkpoint_ref:
            try:
                manager.resolve_ref(item_state.checkpoint_ref)
            except Exception:
                missing_checkpoint_refs.append(item_state.checkpoint_ref)
        if item_state.worktree_path:
            referenced_worktrees.add(item_state.worktree_path)
            if not resolve_repo_path(repo_root, item_state.worktree_path).exists():
                missing_worktrees.append(item_state.worktree_path)

    orphaned_worktrees = _find_orphaned_worktrees(repo_root, run_state.run_id, referenced_worktrees)
    runtime_policy_checks, runtime_policy_details = runtime_policy_integrity(repo_root, run_state)

    checks = {
        "playbook_source_path_exists": playbook_path.exists(),
        "normalized_plan_path_exists": normalized_plan_path.exists(),
        "normalized_plan_valid": normalized_plan_valid if normalized_plan_path.exists() else None,
        "playbook_snapshot_exists": snapshot_path.exists(),
        "run_branch_exists": manager.branch_exists(run_state.run_branch_name),
        "item_branches_exist": not missing_item_branches,
        "checkpoint_refs_exist": not missing_checkpoint_refs,
        "referenced_worktrees_exist": not missing_worktrees,
    }
    checks.update(runtime_policy_checks)

    status = "ok"
    hard_failure = any(
        value is False
        for key, value in checks.items()
        if key not in {"playbook_snapshot_exists", *RUNTIME_POLICY_CHECK_KEYS} and value is not None
    )
    runtime_policy_warning = any(
        checks[key] is False for key in RUNTIME_POLICY_CHECK_KEYS if key in checks
    )
    if hard_failure:
        status = "error"
    elif orphaned_worktrees or runtime_policy_warning:
        status = "warning"

    payload: dict[str, Any] = {
        "name": "run_references",
        "status": status,
        "run_id": run_state.run_id,
        "checks": checks,
        "missing_item_branches": missing_item_branches,
        "missing_checkpoint_refs": missing_checkpoint_refs,
        "missing_worktrees": missing_worktrees,
        "orphaned_worktrees": orphaned_worktrees,
    }
    if normalized_plan_error:
        payload["normalized_plan_error"] = normalized_plan_error
    payload.update(runtime_policy_details)
    return payload


def _find_orphaned_worktrees(
    repo_root: Path,
    run_id: str,
    referenced_worktrees: set[str],
) -> list[str]:
    run_worktrees_root = repo_root / WORKTREES_ROOT / run_id
    if not run_worktrees_root.exists():
        return []

    found: list[str] = []
    for child in sorted(run_worktrees_root.iterdir()):
        if not child.is_dir():
            continue
        rel = child.relative_to(repo_root).as_posix()
        if rel not in referenced_worktrees:
            found.append(rel)
    return found


def _apply_safe_repairs(
    repo_root: Path,
    run_state: RunState,
    reference_report: dict[str, Any],
) -> list[dict[str, Any]]:
    repairs: list[dict[str, Any]] = []
    checks = reference_report["checks"]
    normalized_needs_rebuild = (
        checks["normalized_plan_path_exists"] is False
        or checks["normalized_plan_valid"] is False
    )
    if not normalized_needs_rebuild:
        return repairs

    snapshot_path = resolve_run_directories(repo_root, run_state.run_id).run_root / "playbook_source_snapshot.md"
    if not snapshot_path.exists():
        repairs.append(
            {
                "name": "rebuild_normalized_plan",
                "status": "skipped",
                "detail": f"Missing playbook snapshot: {snapshot_path.relative_to(repo_root).as_posix()}",
            }
        )
        return repairs

    plan = _normalized_plan_from_playbook_snapshot(
        repo_root=repo_root,
        snapshot_path=snapshot_path,
        preserved_playbook_path=Path(run_state.playbook_source_path),
    )
    normalized_plan_path = resolve_repo_path(repo_root, run_state.normalized_plan_path)
    write_json_atomic(normalized_plan_path, plan.to_dict())
    repairs.append(
        {
            "name": "rebuild_normalized_plan",
            "status": "applied",
            "path": normalized_plan_path.relative_to(repo_root).as_posix(),
            "source_snapshot_path": snapshot_path.relative_to(repo_root).as_posix(),
        }
    )
    return repairs


def _normalized_plan_from_playbook_snapshot(
    *,
    repo_root: Path,
    snapshot_path: Path,
    preserved_playbook_path: Path,
):
    snapshot_text = snapshot_path.read_text(encoding="utf-8")
    try:
        playbook_source = snapshot_text.split("\n---\n\n", 1)[1]
    except IndexError as exc:
        raise RuntimeError(
            f"Playbook snapshot is malformed and cannot be repaired: {snapshot_path}"
        ) from exc

    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as handle:
        handle.write(playbook_source)
        temp_path = Path(handle.name)

    try:
        parsed = parse_playbook(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    adapter = build_default_adapter(repo_root)
    return adapter.normalize(parsed, preserved_playbook_path)
