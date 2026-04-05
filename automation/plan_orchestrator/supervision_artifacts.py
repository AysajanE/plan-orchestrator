from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import resolve_run_directories
from .validators import (
    compute_path_sha256,
    compute_sha256,
    ensure_directory,
    load_json,
    repo_relative_path,
    utc_now_iso,
    validate_json_file,
    validate_named_schema,
    write_json_atomic,
)

DEFAULT_LIVE_PROBE_INTERVAL_SEC = 15
DEFAULT_PROBE_ACK_DEADLINE_SEC = 5
DEFAULT_LIVE_STALE_TIMEOUT_SEC = 45
DEFAULT_WAITING_POLL_INTERVAL_SEC = 60
DEFAULT_WAITING_STALE_TIMEOUT_SEC = 180
DEFAULT_MAX_AUTO_RESUME_ATTEMPTS = 2


@dataclass(frozen=True)
class FreshnessPolicy:
    live_probe_interval_sec: int = DEFAULT_LIVE_PROBE_INTERVAL_SEC
    probe_ack_deadline_sec: int = DEFAULT_PROBE_ACK_DEADLINE_SEC
    live_stale_timeout_sec: int = DEFAULT_LIVE_STALE_TIMEOUT_SEC
    waiting_poll_interval_sec: int = DEFAULT_WAITING_POLL_INTERVAL_SEC
    waiting_stale_timeout_sec: int = DEFAULT_WAITING_STALE_TIMEOUT_SEC

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if int(value) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.probe_ack_deadline_sec > self.live_stale_timeout_sec:
            raise ValueError("probe_ack_deadline_sec must be <= live_stale_timeout_sec")
        if self.live_probe_interval_sec > self.live_stale_timeout_sec:
            raise ValueError("live_probe_interval_sec must be <= live_stale_timeout_sec")

    def to_dict(self) -> dict[str, int]:
        return {
            "live_probe_interval_sec": int(self.live_probe_interval_sec),
            "probe_ack_deadline_sec": int(self.probe_ack_deadline_sec),
            "live_stale_timeout_sec": int(self.live_stale_timeout_sec),
            "waiting_poll_interval_sec": int(self.waiting_poll_interval_sec),
            "waiting_stale_timeout_sec": int(self.waiting_stale_timeout_sec),
        }


@dataclass(frozen=True)
class SupervisionPaths:
    repo_root: Path
    run_root: Path
    supervision_root: Path
    bridge_registration_path: Path
    active_stage_path: Path
    probe_request_path: Path
    probe_ack_path: Path
    control_lock_path: Path
    heartbeats_dir: Path
    interventions_dir: Path
    invocations_dir: Path


def resolve_supervision_paths(repo_root: Path, run_id: str) -> SupervisionPaths:
    run_dirs = resolve_run_directories(repo_root, run_id)
    supervision_root = run_dirs.run_root / "supervision"
    return SupervisionPaths(
        repo_root=repo_root,
        run_root=run_dirs.run_root,
        supervision_root=supervision_root,
        bridge_registration_path=supervision_root / "bridge_registration.json",
        active_stage_path=supervision_root / "active_stage.json",
        probe_request_path=supervision_root / "probe_request.json",
        probe_ack_path=supervision_root / "probe_ack.json",
        control_lock_path=supervision_root / "control.lock",
        heartbeats_dir=supervision_root / "heartbeats",
        interventions_dir=supervision_root / "interventions",
        invocations_dir=supervision_root / "invocations",
    )


def ensure_supervision_layout(paths: SupervisionPaths) -> SupervisionPaths:
    ensure_directory(paths.run_root)
    ensure_directory(paths.supervision_root)
    ensure_directory(paths.heartbeats_dir)
    ensure_directory(paths.interventions_dir)
    ensure_directory(paths.invocations_dir)
    return paths


def parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def utc_age_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    parsed = parse_utc_datetime(value)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - parsed).total_seconds())


def _compact_timestamp(value: str) -> str:
    return value.replace("-", "").replace(":", "")


def _next_sequence_from_directory(directory: Path) -> int:
    if not directory.exists():
        return 1
    sequences: list[int] = []
    for path in directory.glob("*.json"):
        prefix = path.name.split("_", 1)[0]
        if prefix.isdigit():
            sequences.append(int(prefix))
    return (max(sequences) + 1) if sequences else 1


def load_control_lock(paths: SupervisionPaths) -> dict[str, Any] | None:
    if not paths.control_lock_path.exists():
        return None
    return load_json(paths.control_lock_path)


def load_bridge_registration(paths: SupervisionPaths) -> dict[str, Any] | None:
    if not paths.bridge_registration_path.exists():
        return None
    return validate_json_file(
        "supervision_bridge_registration.schema.json",
        paths.bridge_registration_path,
    )


def write_bridge_registration(paths: SupervisionPaths, payload: dict[str, Any]) -> Path:
    ensure_supervision_layout(paths)
    payload = dict(payload)
    payload["schema_version"] = "plan_orchestrator.supervision_bridge_registration.v1"
    validate_named_schema("supervision_bridge_registration.schema.json", payload)
    write_json_atomic(paths.bridge_registration_path, payload)
    return paths.bridge_registration_path


def load_active_stage(paths: SupervisionPaths) -> dict[str, Any] | None:
    if not paths.active_stage_path.exists():
        return None
    return validate_json_file(
        "supervision_active_stage.schema.json",
        paths.active_stage_path,
    )


def write_active_stage(paths: SupervisionPaths, payload: dict[str, Any]) -> Path:
    ensure_supervision_layout(paths)
    payload = dict(payload)
    payload["schema_version"] = "plan_orchestrator.supervision_active_stage.v1"
    validate_named_schema("supervision_active_stage.schema.json", payload)
    write_json_atomic(paths.active_stage_path, payload)
    return paths.active_stage_path


def load_probe_request(paths: SupervisionPaths) -> dict[str, Any] | None:
    if not paths.probe_request_path.exists():
        return None
    return validate_json_file(
        "supervision_probe_request.schema.json",
        paths.probe_request_path,
    )


def write_probe_request(paths: SupervisionPaths, payload: dict[str, Any]) -> Path:
    ensure_supervision_layout(paths)
    payload = dict(payload)
    payload["schema_version"] = "plan_orchestrator.supervision_probe_request.v1"
    validate_named_schema("supervision_probe_request.schema.json", payload)
    write_json_atomic(paths.probe_request_path, payload)
    return paths.probe_request_path


def load_probe_ack(paths: SupervisionPaths) -> dict[str, Any] | None:
    if not paths.probe_ack_path.exists():
        return None
    return validate_json_file(
        "supervision_probe_ack.schema.json",
        paths.probe_ack_path,
    )


def write_probe_ack(paths: SupervisionPaths, payload: dict[str, Any]) -> Path:
    ensure_supervision_layout(paths)
    payload = dict(payload)
    payload["schema_version"] = "plan_orchestrator.supervision_probe_ack.v1"
    validate_named_schema("supervision_probe_ack.schema.json", payload)
    write_json_atomic(paths.probe_ack_path, payload)
    return paths.probe_ack_path


def append_heartbeat(paths: SupervisionPaths, payload: dict[str, Any]) -> Path:
    ensure_supervision_layout(paths)
    sequence = int(payload["heartbeat_sequence"])
    observed_at = str(payload["observed_at_utc"])
    filename = f"{sequence:06d}_{_compact_timestamp(observed_at)}.json"
    output_path = paths.heartbeats_dir / filename
    document = dict(payload)
    document["schema_version"] = "plan_orchestrator.supervision_heartbeat.v1"
    validate_named_schema("supervision_heartbeat.schema.json", document)
    write_json_atomic(output_path, document)
    return output_path


def load_heartbeat(path: Path) -> dict[str, Any]:
    return validate_json_file("supervision_heartbeat.schema.json", path)


def list_heartbeat_paths(paths: SupervisionPaths) -> list[Path]:
    if not paths.heartbeats_dir.exists():
        return []
    return sorted(path for path in paths.heartbeats_dir.glob("*.json") if path.is_file())


def load_latest_heartbeat(paths: SupervisionPaths) -> tuple[Path, dict[str, Any]] | None:
    heartbeat_paths = list_heartbeat_paths(paths)
    if not heartbeat_paths:
        return None
    latest = heartbeat_paths[-1]
    return latest, load_heartbeat(latest)


def next_heartbeat_sequence(paths: SupervisionPaths) -> int:
    return _next_sequence_from_directory(paths.heartbeats_dir)


def write_intervention(paths: SupervisionPaths, payload: dict[str, Any]) -> Path:
    ensure_supervision_layout(paths)
    sequence = int(payload["intervention_sequence"])
    action_kind = str(payload["action_kind"]).strip().replace(" ", "_")
    observed_at = str(payload["observed_at_utc"])
    filename = f"{sequence:06d}_{action_kind}_{_compact_timestamp(observed_at)}.json"
    output_path = paths.interventions_dir / filename
    document = dict(payload)
    document["schema_version"] = "plan_orchestrator.supervision_intervention.v1"
    validate_named_schema("supervision_intervention.schema.json", document)
    write_json_atomic(output_path, document)
    return output_path


def load_intervention(path: Path) -> dict[str, Any]:
    return validate_json_file("supervision_intervention.schema.json", path)


def list_intervention_paths(paths: SupervisionPaths) -> list[Path]:
    if not paths.interventions_dir.exists():
        return []
    return sorted(path for path in paths.interventions_dir.glob("*.json") if path.is_file())


def load_latest_intervention(paths: SupervisionPaths) -> tuple[Path, dict[str, Any]] | None:
    intervention_paths = list_intervention_paths(paths)
    if not intervention_paths:
        return None
    latest = intervention_paths[-1]
    return latest, load_intervention(latest)


def next_intervention_sequence(paths: SupervisionPaths) -> int:
    return _next_sequence_from_directory(paths.interventions_dir)


def kernel_snapshot_from_run_state(repo_root: Path, run_state_path: Path) -> dict[str, Any]:
    snapshot = {
        "run_state_path": repo_relative_path(repo_root, run_state_path),
        "run_state_sha256": None,
        "current_state": None,
        "current_item_id": None,
        "current_terminal_state": None,
        "run_state_updated_at_utc": None,
    }
    if not run_state_path.exists():
        return snapshot

    payload = validate_json_file("run_state.schema.json", run_state_path)
    snapshot["run_state_sha256"] = compute_sha256(run_state_path)
    snapshot["current_state"] = payload.get("current_state")
    snapshot["current_item_id"] = payload.get("current_item_id")
    snapshot["run_state_updated_at_utc"] = payload.get("updated_at_utc")

    current_item_id = payload.get("current_item_id")
    if current_item_id is not None:
        for item_state in payload.get("items", []):
            if item_state.get("item_id") == current_item_id:
                snapshot["current_terminal_state"] = item_state.get("terminal_state")
                break
    elif payload.get("current_state") == "ST130_PASSED":
        snapshot["current_terminal_state"] = "passed"
    elif payload.get("current_state") == "ST140_ESCALATED":
        snapshot["current_terminal_state"] = "escalated"
    elif payload.get("current_state") == "ST120_BLOCKED_EXTERNAL":
        snapshot["current_terminal_state"] = "blocked_external"
    elif payload.get("current_state") == "ST110_AWAITING_HUMAN_GATE":
        snapshot["current_terminal_state"] = "awaiting_human_gate"

    return snapshot


def active_stage_snapshot(repo_root: Path, active_stage_path: Path) -> dict[str, Any] | None:
    if not active_stage_path.exists():
        return None
    payload = validate_json_file("supervision_active_stage.schema.json", active_stage_path)
    return {
        "stage_name": payload["stage_name"],
        "item_id": payload["item_id"],
        "attempt_number": payload["attempt_number"],
        "child_tool": payload["child_tool"],
        "child_pid": payload["child_pid"],
        "child_command": payload.get("child_command"),
        "started_at_utc": payload["started_at_utc"],
        "active_stage_path": repo_relative_path(repo_root, active_stage_path),
        "active_stage_sha256": compute_sha256(active_stage_path),
    }


def build_probe_evidence(
    *,
    repo_root: Path,
    paths: SupervisionPaths,
    request: dict[str, Any],
    ack: dict[str, Any] | None,
) -> dict[str, Any]:
    request_sha = compute_sha256(paths.probe_request_path) if paths.probe_request_path.exists() else None
    ack_sha = compute_sha256(paths.probe_ack_path) if paths.probe_ack_path.exists() else None
    bridge_sha = compute_sha256(paths.bridge_registration_path) if paths.bridge_registration_path.exists() else None
    return {
        "probe_sequence": request["probe_sequence"],
        "probe_nonce": request["probe_nonce"],
        "probe_request_issued_at_utc": request["issued_at_utc"],
        "probe_request_expires_at_utc": request["expires_at_utc"],
        "probe_ack_at_utc": None if ack is None else ack.get("acked_at_utc"),
        "bridge_registration_path": repo_relative_path(repo_root, paths.bridge_registration_path),
        "bridge_registration_sha256": bridge_sha,
        "probe_request_sha256": request_sha,
        "probe_ack_sha256": ack_sha,
    }


def diagnosis_snapshot(
    *,
    kernel_status_level: str | None,
    pending_action_kind: str | None,
    recoverability_class: str | None,
    next_supervisor_action: str | None,
) -> dict[str, Any]:
    return {
        "kernel_status_level": kernel_status_level,
        "pending_action_kind": pending_action_kind,
        "recoverability_class": recoverability_class,
        "next_supervisor_action": next_supervisor_action,
    }


def next_sequences(paths: SupervisionPaths) -> tuple[int, int]:
    return next_heartbeat_sequence(paths), next_intervention_sequence(paths)
