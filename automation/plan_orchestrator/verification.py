from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .git_checkpoint import scope_check_for_committed_changes
from .validators import ensure_directory, repo_relative_path, utc_now_iso, validate_named_schema, write_json_atomic


def _verification_environment(
    *,
    repo_root: Path,
    worktree_path: Path,
    item_context: dict[str, Any],
) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("BASH_ENV", "ENV", "PROMPT_COMMAND", "CDPATH"):
        env.pop(key, None)
    env["PLAN_ORCHESTRATOR_VERIFICATION"] = "1"
    for artifact in item_context.get("artifact_inputs", []):
        if artifact.get("logical_name") != "external_evidence":
            continue
        source_path = artifact.get("path")
        workspace_path = artifact.get("workspace_path")
        if source_path:
            env["PLAN_ORCHESTRATOR_EXTERNAL_EVIDENCE_DIR"] = str(
                (repo_root / source_path).resolve()
            )
        if workspace_path:
            env["PLAN_ORCHESTRATOR_EXTERNAL_EVIDENCE_WORKSPACE_DIR"] = str(
                (worktree_path / workspace_path).resolve()
            )
    return env


def _run_shell_command(
    command: str,
    cwd: Path,
    log_path: Path,
    *,
    env: dict[str, str],
    timeout_sec: int,
) -> dict[str, Any]:
    ensure_directory(log_path.parent)
    with log_path.open("w", encoding="utf-8") as handle:
        try:
            completed = subprocess.run(
                ["bash", "-c", command],
                cwd=str(cwd),
                env=env,
                text=True,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            handle.write(f"\nCommand timed out after {timeout_sec} seconds.\n")
            return {
                "exit_code": None,
                "status": "fail",
                "failure_kind": "timeout",
                "timeout_sec": timeout_sec,
            }
    status = "pass" if completed.returncode == 0 else "fail"
    return {
        "exit_code": completed.returncode,
        "status": status,
        "failure_kind": "exit_nonzero" if status == "fail" else None,
        "timeout_sec": None,
    }


def _artifact_check(
    worktree_path: Path,
    check_plan: dict[str, Any],
) -> dict[str, Any]:
    target = worktree_path / check_plan["path"]
    check_kind = check_plan["check_kind"]
    expected_values = list(check_plan.get("expected_values", []))
    reason = check_plan["reason"]

    if check_kind == "exists":
        status = "pass" if target.exists() else "fail"
        return {
            "path": check_plan["path"],
            "check_kind": check_kind,
            "status": status,
            "reason": reason,
        }

    if not target.exists():
        return {
            "path": check_plan["path"],
            "check_kind": check_kind,
            "status": "fail",
            "reason": f"{reason} (target missing)",
        }

    text = target.read_text(encoding="utf-8", errors="replace")
    if check_kind == "contains_substrings":
        ok = all(value in text for value in expected_values)
    elif check_kind == "not_contains_substrings":
        ok = all(value not in text for value in expected_values)
    else:
        ok = False
        reason = f"Unsupported artifact check kind: {check_kind}"

    return {
        "path": check_plan["path"],
        "check_kind": check_kind,
        "status": "pass" if ok else "fail",
        "reason": reason,
    }


def run_verification(
    *,
    repo_root: Path,
    worktree_path: Path,
    item_context: dict[str, Any],
    previous_ref: str,
    current_ref: str,
    report_path: Path,
    logs_dir: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    ensure_directory(logs_dir)
    verification_env = _verification_environment(
        repo_root=repo_root,
        worktree_path=worktree_path,
        item_context=item_context,
    )

    command_results: list[dict[str, Any]] = []
    for index, group in enumerate(item_context["verification_plan"]["command_groups"], start=1):
        commands = list(group.get("commands", []))
        if not commands:
            command_results.append(
                {
                    "label": group["label"],
                    "command": "<no commands>",
                    "exit_code": None,
                    "status": "skipped",
                    "failure_kind": None,
                    "required": bool(group.get("required", False)),
                    "log_path": None,
                    "timeout_sec": None,
                }
            )
            continue

        for command_index, command in enumerate(commands, start=1):
            log_path = logs_dir / f"{index:02d}_{command_index:02d}.log"
            result = _run_shell_command(
                command,
                worktree_path,
                log_path,
                env=verification_env,
                timeout_sec=timeout_sec,
            )
            command_results.append(
                {
                    "label": f"{group['label']}#{command_index}",
                    "command": command,
                    "exit_code": result["exit_code"],
                    "status": result["status"],
                    "failure_kind": result["failure_kind"],
                    "required": bool(group.get("required", False)),
                    "log_path": repo_relative_path(repo_root, log_path),
                    "timeout_sec": result["timeout_sec"],
                }
            )

    artifact_results = [
        _artifact_check(worktree_path, check_plan)
        for check_plan in item_context["verification_plan"]["artifact_checks"]
    ]

    scope_note, out_of_scope = scope_check_for_committed_changes(
        worktree_path=worktree_path,
        base_ref=previous_ref,
        target_ref=current_ref,
        allowed_write_roots=item_context["repo_scope"]["allowed_write_roots"],
    )

    any_required_command_fail = any(
        result["status"] == "fail" and result.get("required", False)
        for result in command_results
    )
    any_required_command_timeout = any(
        result["failure_kind"] == "timeout" and result.get("required", False)
        for result in command_results
    )
    any_optional_command_fail = any(
        result["status"] == "fail" and not result.get("required", False)
        for result in command_results
    )
    any_optional_command_timeout = any(
        result["failure_kind"] == "timeout" and not result.get("required", False)
        for result in command_results
    )
    any_artifact_fail = any(result["status"] == "fail" for result in artifact_results)
    scope_failed = bool(out_of_scope)

    overall_result = "pass"
    next_recommended_state = "audit"
    summary = "Verification passed."

    if scope_failed:
        overall_result = "fail"
        next_recommended_state = "escalate"
        summary = "Verification failed because committed changes escaped allowed write roots."
    elif any_required_command_fail or any_artifact_fail:
        overall_result = "fail"
        next_recommended_state = "fix"
        if any_required_command_timeout:
            summary = "Verification failed because one or more required commands timed out or other checks failed."
        else:
            summary = "Verification failed because one or more commands or artifact checks failed."
    elif any_optional_command_fail:
        overall_result = "partial"
        next_recommended_state = "audit"
        if any_optional_command_timeout:
            summary = "Verification completed with optional command timeouts."
        else:
            summary = "Verification completed with optional command failures."
    elif any(result["status"] == "skipped" for result in command_results):
        overall_result = "partial"
        next_recommended_state = "audit"
        summary = "Verification passed with skipped optional command groups."

    report = {
        "schema_version": "plan_orchestrator.verification_report.v1",
        "generated_at_utc": utc_now_iso(),
        "stage": "verify",
        "item_id": item_context["item"]["item_id"],
        "attempt_number": int(item_context["stage_context"]["attempt_number"]),
        "summary": summary,
        "overall_result": overall_result,
        "command_results": command_results,
        "artifact_checks": artifact_results,
        "scope_check": {
            "status": "fail" if scope_failed else "pass",
            "out_of_scope_paths": out_of_scope,
            "note": scope_note,
        },
        "next_recommended_state": next_recommended_state,
    }
    validate_named_schema("verification_report.schema.json", report)
    write_json_atomic(report_path, report)
    return report
