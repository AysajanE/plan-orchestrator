from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .validators import (
    compute_path_sha256,
    ensure_directory,
    repo_relative_path,
    resolve_repo_path,
    validate_named_schema,
    write_json_atomic,
)


LOCAL_STORAGE_CLASSES = {"local_run_control", "local_model_report", "human_supplied_local"}


def artifact_spec(
    *,
    logical_name: str,
    path: str | Path,
    content_type: str,
    storage_class: str,
    git_policy: str,
    trust_level: str,
    producer: str,
    consumers: list[str],
    must_exist: bool,
    description: str,
) -> dict[str, Any]:
    return {
        "logical_name": logical_name,
        "path": str(path),
        "content_type": content_type,
        "storage_class": storage_class,
        "git_policy": git_policy,
        "trust_level": trust_level,
        "producer": producer,
        "consumers": consumers,
        "must_exist": must_exist,
        "description": description,
    }


def _packet_relative_path(logical_name: str, source_path: Path) -> Path:
    return Path(".local") / "plan_orchestrator" / "packet" / "artifacts" / logical_name / source_path.name


def workspace_path_for_artifact(
    *,
    repo_root: Path,
    worktree_root: Path,
    logical_name: str,
    path: str | Path,
    storage_class: str,
) -> str | None:
    source = resolve_repo_path(repo_root, path)
    if storage_class == "tracked_repo":
        return repo_relative_path(repo_root, source)
    return _packet_relative_path(logical_name, source).as_posix()


def _materialize_packet_copy(
    *,
    repo_root: Path,
    worktree_root: Path,
    packet_root: Path,
    logical_name: str,
    source_path: Path,
    storage_class: str,
) -> str | None:
    if storage_class == "tracked_repo":
        return repo_relative_path(repo_root, source_path)

    target = worktree_root / _packet_relative_path(logical_name, source_path)
    ensure_directory(target.parent)

    if source_path.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source_path, target)
    else:
        shutil.copy2(source_path, target)

    return target.relative_to(worktree_root).as_posix()


def build_artifact_manifest(
    *,
    repo_root: Path,
    worktree_root: Path,
    packet_root: Path,
    run_id: str,
    item_id: str,
    attempt_number: int,
    producer_stage: str,
    artifact_specs: list[dict[str, Any]],
    output_path: Path,
) -> tuple[dict[str, Any], str]:
    ensure_directory(output_path.parent)
    ensure_directory(packet_root)

    artifacts: list[dict[str, Any]] = []
    for spec in artifact_specs:
        source_path = resolve_repo_path(repo_root, spec["path"])
        exists = source_path.exists()
        if spec["must_exist"] and not exists:
            raise FileNotFoundError(f"Required artifact is missing: {source_path}")

        workspace_packet_path = None
        sha = None
        if exists:
            workspace_packet_path = _materialize_packet_copy(
                repo_root=repo_root,
                worktree_root=worktree_root,
                packet_root=packet_root,
                logical_name=spec["logical_name"],
                source_path=source_path,
                storage_class=spec["storage_class"],
            )
            sha = compute_path_sha256(source_path)

        entry = {
            "logical_name": spec["logical_name"],
            "path": repo_relative_path(repo_root, source_path),
            "workspace_packet_path": workspace_packet_path,
            "content_type": spec["content_type"],
            "storage_class": spec["storage_class"],
            "git_policy": spec["git_policy"],
            "trust_level": spec["trust_level"],
            "producer": spec["producer"],
            "consumers": list(spec["consumers"]),
            "must_exist": bool(spec["must_exist"]),
            "sha256": sha,
            "visibility_bridge": (
                "direct-worktree-path"
                if spec["storage_class"] == "tracked_repo"
                else "packet-copy-sha256-pinned"
            ),
            "description": spec["description"],
        }
        artifacts.append(entry)

    manifest = {
        "schema_version": "plan_orchestrator.artifact_manifest.v1",
        "generated_at_utc": __import__("datetime").datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "run_id": run_id,
        "item_id": item_id,
        "attempt_number": attempt_number,
        "producer_stage": producer_stage,
        "artifacts": artifacts,
    }
    validate_named_schema("artifact_manifest.schema.json", manifest)
    write_json_atomic(output_path, manifest)

    manifest_packet_path = worktree_root / ".local" / "plan_orchestrator" / "packet" / output_path.name
    ensure_directory(manifest_packet_path.parent)
    shutil.copy2(output_path, manifest_packet_path)
    return manifest, manifest_packet_path.relative_to(worktree_root).as_posix()


def workspace_path_for_manifest_entry(manifest: dict[str, Any], logical_name: str) -> str:
    for entry in manifest.get("artifacts", []):
        if entry["logical_name"] == logical_name:
            if entry["workspace_packet_path"] is None:
                return entry["path"]
            return entry["workspace_packet_path"]
    raise KeyError(f"Artifact not found in manifest: {logical_name}")


def render_template(
    *,
    template_path: Path,
    output_path: Path,
    variables: dict[str, Any],
) -> None:
    ensure_directory(output_path.parent)
    text = template_path.read_text(encoding="utf-8")
    for key, value in variables.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, indent=2, sort_keys=False)
        else:
            rendered = str(value)
        text = text.replace(f"{{{{{key}}}}}", rendered)
    output_path.write_text(text, encoding="utf-8")


def write_playbook_snapshot(
    *,
    source_path: Path,
    source_sha256: str,
    source_text: str,
    output_path: Path,
    snapshot_note: str | None = None,
) -> None:
    ensure_directory(output_path.parent)
    note_block = ""
    if snapshot_note:
        note_block = f"- snapshot_note: {snapshot_note}\n\n"
    payload = (
        "# Playbook Source Snapshot\n\n"
        f"- source_path: `{source_path.as_posix()}`\n"
        f"- sha256: `{source_sha256}`\n\n"
        f"{note_block}"
        "---\n\n"
        f"{source_text}"
    )
    output_path.write_text(payload, encoding="utf-8")


def write_manual_gate_record(
    *,
    output_path: Path,
    run_id: str,
    item_id: str,
    gate_id: str,
    gate_type: str,
    status: str,
    requested_by: str,
    requested_reason: str,
    required_evidence: list[str],
    branch_name: str | None,
    worktree_path: str | None,
    checkpoint_ref: str | None,
    artifact_manifest_path: str,
    triage_report_path: str | None,
    merged_findings_packet_path: str | None,
    codex_audit_report_path: str | None,
    claude_audit_report_path: str | None,
    review_findings: list[dict[str, Any]] | None,
    decision: dict[str, Any] | None,
    requested_at_utc: str | None = None,
) -> dict[str, Any]:
    if requested_at_utc is None and output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        requested_at_utc = existing.get("requested_at_utc")

    if requested_at_utc is None:
        requested_at_utc = __import__("datetime").datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    payload = {
        "schema_version": "plan_orchestrator.manual_gate.v1",
        "requested_at_utc": requested_at_utc,
        "run_id": run_id,
        "item_id": item_id,
        "gate_id": gate_id,
        "gate_type": gate_type,
        "status": status,
        "requested_by": requested_by,
        "requested_reason": requested_reason,
        "required_evidence": required_evidence,
        "review_findings": review_findings or [],
        "related_refs": {
            "branch_name": branch_name,
            "worktree_path": worktree_path,
            "checkpoint_ref": checkpoint_ref,
            "artifact_manifest_path": artifact_manifest_path,
            "triage_report_path": triage_report_path,
            "merged_findings_packet_path": merged_findings_packet_path,
            "codex_audit_report_path": codex_audit_report_path,
            "claude_audit_report_path": claude_audit_report_path,
        },
        "decision": decision,
    }
    validate_named_schema("manual_gate.schema.json", payload)
    write_json_atomic(output_path, payload)
    return payload


def write_escalation_manifest(
    *,
    output_path: Path,
    run_id: str,
    item_id: str,
    attempt_number: int,
    terminal_state: str,
    summary: str,
    blocking_reasons: list[str],
    required_human_actions: list[dict[str, Any]],
    branch_name: str | None,
    worktree_path: str | None,
    checkpoint_ref: str | None,
    run_state_path: str,
    primary_report_paths: list[str],
    artifact_manifest_path: str | None,
    suggested_resume_command: str,
    notes: list[str],
) -> dict[str, Any]:
    payload = {
        "schema_version": "plan_orchestrator.escalation_manifest.v1",
        "generated_at_utc": __import__("datetime").datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "terminal_state": terminal_state,
        "run_id": run_id,
        "item_id": item_id,
        "attempt_number": attempt_number,
        "summary": summary,
        "blocking_reasons": blocking_reasons,
        "required_human_actions": required_human_actions,
        "preserved_refs": {
            "branch_name": branch_name,
            "worktree_path": worktree_path,
            "checkpoint_ref": checkpoint_ref,
            "run_state_path": run_state_path,
            "primary_report_paths": primary_report_paths,
        },
        "artifact_manifest_path": artifact_manifest_path,
        "suggested_resume_command": suggested_resume_command,
        "notes": notes,
    }
    validate_named_schema("escalation_manifest.schema.json", payload)
    write_json_atomic(output_path, payload)
    return payload


def write_pass_summary(
    *,
    output_path: Path,
    run_id: str,
    item_id: str,
    attempt_number: int,
    summary: str,
    artifact_manifest_path: str,
    checkpoint_ref: str | None,
) -> None:
    ensure_directory(output_path.parent)
    payload = (
        "# Passed Item Summary\n\n"
        f"- run_id: `{run_id}`\n"
        f"- item_id: `{item_id}`\n"
        f"- attempt_number: `{attempt_number}`\n"
        f"- checkpoint_ref: `{checkpoint_ref or ''}`\n"
        f"- artifact_manifest_path: `{artifact_manifest_path}`\n\n"
        "## Summary\n\n"
        f"{summary}\n"
    )
    output_path.write_text(payload, encoding="utf-8")
