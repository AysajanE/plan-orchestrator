from __future__ import annotations

from pathlib import Path
from typing import Any

from .status import load_run_status
from .supervision_artifacts import (
    active_stage_snapshot,
    load_bridge_registration,
    load_latest_heartbeat,
    load_latest_intervention,
    resolve_supervision_paths,
    utc_age_seconds,
)
from .validators import repo_relative_path

SUPERVISE_STATUS_EXIT_CODES = {
    "live_attached": 0,
    "waiting_state_observed": 10,
    "attachment_unproven": 11,
    "terminal_observed": 12,
    "snapshot_only": 13,
}


def _latest_artifacts(repo_root: Path, run_id: str) -> dict[str, Any]:
    paths = resolve_supervision_paths(repo_root, run_id)

    bridge_registration = None
    bridge_error = None
    try:
        bridge_registration = load_bridge_registration(paths)
    except Exception as exc:
        bridge_error = str(exc)

    latest_heartbeat_path = None
    latest_heartbeat = None
    latest_heartbeat_error = None
    try:
        heartbeat_pair = load_latest_heartbeat(paths)
        if heartbeat_pair is not None:
            latest_heartbeat_path, latest_heartbeat = heartbeat_pair
    except Exception as exc:
        latest_heartbeat_error = str(exc)

    active_stage = None
    active_stage_error = None
    try:
        active_stage = active_stage_snapshot(repo_root, paths.active_stage_path)
    except Exception as exc:
        active_stage_error = str(exc)

    latest_intervention_path = None
    latest_intervention = None
    try:
        intervention_pair = load_latest_intervention(paths)
        if intervention_pair is not None:
            latest_intervention_path, latest_intervention = intervention_pair
    except Exception:
        latest_intervention = None

    return {
        "paths": paths,
        "bridge_registration": bridge_registration,
        "bridge_error": bridge_error,
        "latest_heartbeat_path": latest_heartbeat_path,
        "latest_heartbeat": latest_heartbeat,
        "latest_heartbeat_error": latest_heartbeat_error,
        "active_stage": active_stage,
        "active_stage_error": active_stage_error,
        "latest_intervention_path": latest_intervention_path,
        "latest_intervention": latest_intervention,
    }


def _derive_claim_class(artifacts: dict[str, Any]) -> tuple[str, str]:
    latest_heartbeat = artifacts["latest_heartbeat"]
    bridge_registration = artifacts["bridge_registration"]

    if latest_heartbeat is None:
        if bridge_registration is not None:
            return (
                "attachment_unproven",
                "A bridge registration exists, but no validated heartbeat ledger entry is available yet.",
            )
        if artifacts["bridge_error"] or artifacts["latest_heartbeat_error"] or artifacts["active_stage_error"]:
            return (
                "snapshot_only",
                "Supervision artifacts are missing or invalid; only snapshot truth is available.",
            )
        return (
            "snapshot_only",
            "No supervision heartbeat ledger exists for this run.",
        )

    latest_claim_class = latest_heartbeat["claim_class"]
    freshness_policy = latest_heartbeat["freshness_policy"]
    heartbeat_age_sec = utc_age_seconds(latest_heartbeat.get("observed_at_utc"))

    if latest_claim_class == "live_attached":
        if heartbeat_age_sec is not None and heartbeat_age_sec <= freshness_policy["live_stale_timeout_sec"]:
            return "live_attached", "Fresh probe/ack evidence still falls within the live stale timeout."
        if bridge_registration is not None:
            return (
                "attachment_unproven",
                "The last live heartbeat is stale, and fresh live attachment is no longer proven.",
            )
        return (
            "snapshot_only",
            "The last live heartbeat is stale and no current bridge registration is present.",
        )

    if latest_claim_class == "waiting_state_observed":
        if heartbeat_age_sec is not None and heartbeat_age_sec <= freshness_policy["waiting_stale_timeout_sec"]:
            return (
                "waiting_state_observed",
                "The supervisor recently observed a truthful waiting state.",
            )
        return (
            "snapshot_only",
            "The last waiting-state heartbeat is stale.",
        )

    if latest_claim_class == "attachment_unproven":
        if bridge_registration is not None:
            return (
                "attachment_unproven",
                "Fresh live attachment is still unproven for the current bridge registration.",
            )
        if heartbeat_age_sec is not None and heartbeat_age_sec <= freshness_policy["live_stale_timeout_sec"]:
            return (
                "attachment_unproven",
                "The latest supervisory observation explicitly failed closed on live attachment.",
            )
        return (
            "snapshot_only",
            "The latest attachment-unproven heartbeat is stale and no current bridge registration is present.",
        )

    if latest_claim_class == "terminal_observed":
        return (
            "terminal_observed",
            "The supervisor observed a terminal completion or parked case.",
        )

    return (
        "snapshot_only",
        "The latest supervisory observation is snapshot-only.",
    )


def build_supervision_status(repo_root: Path, run_id: str) -> dict[str, Any]:
    kernel_status = load_run_status(repo_root, run_id)
    artifacts = _latest_artifacts(repo_root, run_id)
    claim_class, reason = _derive_claim_class(artifacts)
    latest_heartbeat = artifacts["latest_heartbeat"]
    bridge_registration = artifacts["bridge_registration"]

    active_stage = artifacts["active_stage"]
    if active_stage is None and latest_heartbeat is not None:
        active_stage = latest_heartbeat.get("active_stage")

    freshness_policy = None if latest_heartbeat is None else latest_heartbeat.get("freshness_policy")

    return {
        "run_id": run_id,
        "kernel_status": kernel_status,
        "supervision_status": {
            "claim_class": claim_class,
            "last_heartbeat_claim_class": None if latest_heartbeat is None else latest_heartbeat.get("claim_class"),
            "exit_code": SUPERVISE_STATUS_EXIT_CODES[claim_class],
            "reason": reason,
            "supervisor_session_id": (
                bridge_registration.get("supervisor_session_id")
                if bridge_registration is not None
                else (None if latest_heartbeat is None else latest_heartbeat.get("supervisor_session_id"))
            ),
            "kernel_invocation_id": (
                bridge_registration.get("kernel_invocation_id")
                if bridge_registration is not None
                else (None if latest_heartbeat is None else latest_heartbeat.get("kernel_invocation_id"))
            ),
            "bridge_registration_path": None
            if bridge_registration is None
            else repo_relative_path(repo_root, artifacts["paths"].bridge_registration_path),
            "latest_heartbeat_path": None
            if artifacts["latest_heartbeat_path"] is None
            else repo_relative_path(repo_root, artifacts["latest_heartbeat_path"]),
            "latest_heartbeat_observed_at_utc": None
            if latest_heartbeat is None
            else latest_heartbeat.get("observed_at_utc"),
            "latest_intervention_path": None
            if artifacts["latest_intervention_path"] is None
            else repo_relative_path(repo_root, artifacts["latest_intervention_path"]),
            "latest_intervention": artifacts["latest_intervention"],
            "active_stage": active_stage,
            "freshness_policy": freshness_policy,
            "last_rejection_reason": None if latest_heartbeat is None else latest_heartbeat.get("rejection_reason"),
        },
    }


def render_supervision_status_text(summary: dict[str, Any]) -> str:
    kernel = summary["kernel_status"]
    supervision = summary["supervision_status"]

    lines = [
        f"RUN {summary['run_id']}",
        f"Supervision: {supervision['claim_class']} (exit {supervision['exit_code']})",
        f"Reason: {supervision['reason']}",
        f"Kernel status: {kernel.get('status_level', 'unknown')} (kernel exit {kernel.get('exit_code', 0)})",
        f"Kernel state: {kernel.get('current_state') or 'unknown'}",
        f"Current item: {kernel.get('current_item_id') or 'none'}",
    ]

    if supervision.get("latest_heartbeat_path"):
        lines.append(f"Latest heartbeat: {supervision['latest_heartbeat_path']}")
    if supervision.get("latest_heartbeat_observed_at_utc"):
        lines.append(f"Latest heartbeat observed_at_utc: {supervision['latest_heartbeat_observed_at_utc']}")
    if supervision.get("bridge_registration_path"):
        lines.append(f"Bridge registration: {supervision['bridge_registration_path']}")
    if supervision.get("latest_intervention_path"):
        lines.append(f"Latest intervention: {supervision['latest_intervention_path']}")

    active_stage = supervision.get("active_stage")
    if active_stage:
        lines.append(
            "Active stage: "
            f"{active_stage['stage_name']} "
            f"(item={active_stage['item_id']}, "
            f"attempt={active_stage['attempt_number']}, "
            f"tool={active_stage['child_tool']}, "
            f"pid={active_stage['child_pid']})"
        )

    return "\n".join(lines)
