from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import SUPERVISED_RUN_ID_OVERRIDE_ENV, make_run_id
from .doctor import run_doctor
from .status import load_run_status
from .supervision_artifacts import (
    DEFAULT_MAX_AUTO_RESUME_ATTEMPTS,
    FreshnessPolicy,
    SupervisionPaths,
    append_heartbeat,
    build_probe_evidence,
    diagnosis_snapshot,
    kernel_snapshot_from_run_state,
    load_bridge_registration,
    load_control_lock,
    load_probe_ack,
    next_sequences,
    parse_utc_datetime,
    resolve_supervision_paths,
    write_intervention,
    write_probe_request,
)
from .supervision_bridge import (
    KERNEL_INVOCATION_ID_ENV,
    SUPERVISION_ENABLED_ENV,
    SUPERVISOR_SESSION_ID_ENV,
)
from .supervision_recovery import RecoveryDecision, classify_recovery
from .supervision_status import build_supervision_status
from .validators import compute_sha256, repo_relative_path, resolve_repo_path, utc_now_iso


class SupervisorError(RuntimeError):
    pass


@dataclass
class KernelInvocation:
    kernel_invocation_id: str
    pid: int
    command: list[str]
    started_at_utc: str
    stdout_path: Path | None
    stderr_path: Path | None
    process: subprocess.Popen[str] | None = None


@dataclass
class ProbeResult:
    request: dict[str, Any]
    ack: dict[str, Any] | None
    valid: bool
    rejection_reason: str | None
    kernel_snapshot: dict[str, Any]
    active_stage: dict[str, Any] | None
    probe_evidence: dict[str, Any]


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _safe_json_write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")


def validate_probe_roundtrip(
    *,
    repo_root: Path,
    paths: SupervisionPaths,
    request: dict[str, Any],
    ack: dict[str, Any] | None,
    previous_observed_at_utc: str | None,
) -> tuple[bool, str | None, dict[str, Any], dict[str, Any] | None]:
    kernel_snapshot = kernel_snapshot_from_run_state(repo_root, paths.run_root / "run_state.json")
    bridge_registration = load_bridge_registration(paths)
    active_stage = None
    try:
        from .supervision_artifacts import active_stage_snapshot

        active_stage = active_stage_snapshot(repo_root, paths.active_stage_path)
    except Exception:
        active_stage = None

    if bridge_registration is None:
        return False, "bridge_registration_missing", kernel_snapshot, active_stage

    if ack is None:
        return False, "probe_ack_missing", kernel_snapshot, active_stage

    for field_name in (
        "run_id",
        "supervisor_session_id",
        "kernel_invocation_id",
        "probe_sequence",
        "probe_nonce",
    ):
        if ack.get(field_name) != request.get(field_name):
            return False, f"probe_ack_mismatch:{field_name}", kernel_snapshot, active_stage

    if bridge_registration.get("supervisor_session_id") != request.get("supervisor_session_id"):
        return False, "bridge_registration_session_mismatch", kernel_snapshot, active_stage
    if bridge_registration.get("kernel_invocation_id") != request.get("kernel_invocation_id"):
        return False, "bridge_registration_invocation_mismatch", kernel_snapshot, active_stage

    acked_at = parse_utc_datetime(ack.get("acked_at_utc"))
    expires_at = parse_utc_datetime(request.get("expires_at_utc"))
    if acked_at is None or expires_at is None:
        return False, "probe_ack_invalid_timestamp", kernel_snapshot, active_stage
    if acked_at > expires_at:
        return False, "probe_ack_expired", kernel_snapshot, active_stage

    previous_observed_at = parse_utc_datetime(previous_observed_at_utc)
    if previous_observed_at is not None and acked_at <= previous_observed_at:
        return False, "probe_ack_not_fresh", kernel_snapshot, active_stage

    ack_snapshot = ack.get("kernel_snapshot", {})
    if kernel_snapshot["run_state_sha256"] is None:
        return False, "run_state_missing", kernel_snapshot, active_stage
    if ack_snapshot.get("run_state_sha256") != kernel_snapshot["run_state_sha256"]:
        return False, "run_state_sha256_mismatch", kernel_snapshot, active_stage
    if ack_snapshot.get("run_state_path") != kernel_snapshot["run_state_path"]:
        return False, "run_state_path_mismatch", kernel_snapshot, active_stage
    if ack_snapshot.get("current_state") != kernel_snapshot["current_state"]:
        return False, "current_state_mismatch", kernel_snapshot, active_stage
    if ack_snapshot.get("current_item_id") != kernel_snapshot["current_item_id"]:
        return False, "current_item_id_mismatch", kernel_snapshot, active_stage

    ack_active_stage_present = bool(ack_snapshot.get("active_stage_present"))
    if active_stage is None and ack_active_stage_present:
        return False, "active_stage_missing", kernel_snapshot, active_stage
    if active_stage is not None:
        if not ack_active_stage_present:
            return False, "active_stage_presence_mismatch", kernel_snapshot, active_stage
        if ack_snapshot.get("active_stage_sha256") != active_stage["active_stage_sha256"]:
            return False, "active_stage_sha256_mismatch", kernel_snapshot, active_stage
        if ack_snapshot.get("active_stage_path") != active_stage["active_stage_path"]:
            return False, "active_stage_path_mismatch", kernel_snapshot, active_stage

    return True, None, kernel_snapshot, active_stage


class RunSupervisor:
    def __init__(
        self,
        *,
        repo_root: Path,
        run_id: str,
        supervisor_session_id: str,
        mode: str,
        evidence_inbox_dir: str | None,
        explicit_external_evidence_dir: str | None,
        resume_auto_advance_override: bool | None,
        freshness_policy: FreshnessPolicy,
        max_auto_resume_attempts: int | None,
        max_wait_seconds: int | None,
    ) -> None:
        self.repo_root = repo_root
        self.run_id = run_id
        self.supervisor_session_id = supervisor_session_id
        self.mode = mode
        self.evidence_inbox_dir = evidence_inbox_dir
        self.explicit_external_evidence_dir = explicit_external_evidence_dir
        self.resume_auto_advance_override = resume_auto_advance_override
        self.freshness_policy = freshness_policy
        self.max_auto_resume_attempts = (
            DEFAULT_MAX_AUTO_RESUME_ATTEMPTS
            if max_auto_resume_attempts is None
            else max_auto_resume_attempts
        )
        self.max_wait_seconds = max_wait_seconds
        self.paths = resolve_supervision_paths(repo_root, run_id)
        self.paths.run_root.mkdir(parents=True, exist_ok=True)
        self.paths.supervision_root.mkdir(parents=True, exist_ok=True)

        heartbeat_sequence, intervention_sequence = next_sequences(self.paths)
        self._heartbeat_sequence = heartbeat_sequence
        self._intervention_sequence = intervention_sequence
        self._probe_sequence = 1
        self._latest_heartbeat_path: Path | None = None
        self._latest_intervention_path: Path | None = None
        self._last_heartbeat_observed_at_utc: str | None = None
        self._prior_wait_action_kind: str | None = None
        self._initial_resume_consumed = False
        self._session_started_monotonic = time.monotonic()

        latest_heartbeat_pair = None
        try:
            from .supervision_artifacts import load_latest_heartbeat

            latest_heartbeat_pair = load_latest_heartbeat(self.paths)
        except Exception:
            latest_heartbeat_pair = None
        if latest_heartbeat_pair is not None:
            self._latest_heartbeat_path, latest_heartbeat = latest_heartbeat_pair
            self._last_heartbeat_observed_at_utc = latest_heartbeat.get("observed_at_utc")
            probe_evidence = latest_heartbeat.get("probe_evidence") or {}
            if probe_evidence.get("probe_sequence") is not None:
                self._probe_sequence = int(probe_evidence["probe_sequence"]) + 1

    def acquire_control_lock(self) -> None:
        payload = {
            "run_id": self.run_id,
            "supervisor_session_id": self.supervisor_session_id,
            "owner_pid": os.getpid(),
            "acquired_at_utc": utc_now_iso(),
        }
        try:
            _safe_json_write_exclusive(self.paths.control_lock_path, payload)
            return
        except FileExistsError:
            existing = load_control_lock(self.paths)
            if existing is None:
                self.paths.control_lock_path.unlink(missing_ok=True)
                _safe_json_write_exclusive(self.paths.control_lock_path, payload)
                return

            existing_session = existing.get("supervisor_session_id")
            existing_owner_pid = int(existing.get("owner_pid") or 0)

            if existing_session == self.supervisor_session_id and existing_owner_pid == os.getpid():
                self.paths.control_lock_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                return

            if existing_session == self.supervisor_session_id and not _pid_is_running(existing_owner_pid):
                self.paths.control_lock_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                return

            if _pid_is_running(existing_owner_pid):
                raise SupervisorError(
                    f"Another supervisor process still owns {repo_relative_path(self.repo_root, self.paths.control_lock_path)}."
                )

            self.paths.control_lock_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def release_control_lock(self) -> None:
        existing = load_control_lock(self.paths)
        if existing is None:
            return
        if int(existing.get("owner_pid") or 0) != os.getpid():
            return
        self.paths.control_lock_path.unlink(missing_ok=True)

    def _build_child_env(self, *, kernel_invocation_id: str, run_id_override: str | None) -> dict[str, str]:
        env = dict(os.environ)
        for key in (
            SUPERVISION_ENABLED_ENV,
            SUPERVISOR_SESSION_ID_ENV,
            KERNEL_INVOCATION_ID_ENV,
            SUPERVISED_RUN_ID_OVERRIDE_ENV,
        ):
            env.pop(key, None)
        env[SUPERVISION_ENABLED_ENV] = "1"
        env[SUPERVISOR_SESSION_ID_ENV] = self.supervisor_session_id
        env[KERNEL_INVOCATION_ID_ENV] = kernel_invocation_id
        if run_id_override:
            env[SUPERVISED_RUN_ID_OVERRIDE_ENV] = run_id_override
        return env

    def _spawn_kernel(self, *, argv: list[str], run_id_override: str | None) -> KernelInvocation:
        kernel_invocation_id = f"kernel_{uuid4().hex}"
        stdout_path = self.paths.invocations_dir / f"{kernel_invocation_id}.stdout.log"
        stderr_path = self.paths.invocations_dir / f"{kernel_invocation_id}.stderr.log"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)

        stdout_handle = stdout_path.open("w", encoding="utf-8")
        stderr_handle = stderr_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [sys.executable, str(self.repo_root / "automation" / "run_plan_orchestrator.py"), *argv],
                cwd=str(self.repo_root),
                env=self._build_child_env(
                    kernel_invocation_id=kernel_invocation_id,
                    run_id_override=run_id_override,
                ),
                text=True,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()

        return KernelInvocation(
            kernel_invocation_id=kernel_invocation_id,
            pid=process.pid,
            command=[sys.executable, "automation/run_plan_orchestrator.py", *argv],
            started_at_utc=utc_now_iso(),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            process=process,
        )

    def spawn_supervised_run(
        self,
        *,
        playbook_path: str,
        item_id: str | None,
        item_ids: list[str] | None,
        next_only: bool,
        config_path: str | None,
        external_evidence_dir: str | None,
        auto_advance: bool | None,
        max_items: int | None,
    ) -> KernelInvocation:
        argv = ["run", "--playbook", playbook_path]
        if item_id:
            argv.extend(["--item", item_id])
        elif item_ids:
            argv.extend(["--items", ",".join(item_ids)])
        elif next_only:
            argv.append("--next")
        else:
            raise SupervisorError("supervise run requires exactly one of --item, --items, or --next")

        if config_path:
            argv.extend(["--config", config_path])
        if external_evidence_dir:
            argv.extend(["--external-evidence-dir", external_evidence_dir])
        if auto_advance:
            argv.append("--auto-advance")
        if max_items is not None:
            argv.extend(["--max-items", str(max_items)])

        invocation = self._spawn_kernel(argv=argv, run_id_override=self.run_id)
        self._write_intervention(
            action_kind="attach_kernel",
            recoverability_class="observed",
            result_status="applied",
            reason="Started a new supervised kernel run invocation.",
            fingerprint=None,
            item_id=None,
            attempt_number=None,
            terminal_state=None,
            command=invocation.command,
            related_paths=[repo_relative_path(self.repo_root, invocation.stdout_path), repo_relative_path(self.repo_root, invocation.stderr_path)],
        )
        return invocation

    def spawn_supervised_resume(self, *, external_evidence_dir: str | None, auto_advance: bool) -> KernelInvocation:
        argv = ["resume", "--run-id", self.run_id]
        if external_evidence_dir:
            argv.extend(["--external-evidence-dir", external_evidence_dir])
        if auto_advance:
            argv.append("--auto-advance")

        invocation = self._spawn_kernel(argv=argv, run_id_override=None)
        self._initial_resume_consumed = True
        self._write_intervention(
            action_kind="attach_kernel",
            recoverability_class="observed",
            result_status="applied",
            reason="Started a supervised kernel resume invocation.",
            fingerprint=None,
            item_id=None,
            attempt_number=None,
            terminal_state=None,
            command=invocation.command,
            related_paths=[repo_relative_path(self.repo_root, invocation.stdout_path), repo_relative_path(self.repo_root, invocation.stderr_path)],
        )
        return invocation

    def attach_existing_live_kernel(self) -> KernelInvocation | None:
        bridge_registration = load_bridge_registration(self.paths)
        if bridge_registration is None:
            return None
        pid = int(bridge_registration.get("bridge_pid") or 0)
        if not _pid_is_running(pid):
            return None
        return KernelInvocation(
            kernel_invocation_id=str(bridge_registration["kernel_invocation_id"]),
            pid=pid,
            command=["<attached-live-kernel>"],
            started_at_utc=str(bridge_registration["bridge_started_at_utc"]),
            stdout_path=None,
            stderr_path=None,
            process=None,
        )

    def _wait_expired(self) -> bool:
        if self.max_wait_seconds is None:
            return False
        elapsed = time.monotonic() - self._session_started_monotonic
        return elapsed >= self.max_wait_seconds

    def _load_run_state_safely(self):
        try:
            from .state_store import load_run_state

            return load_run_state(self.paths.run_root / "run_state.json")
        except Exception:
            return None

    def _allow_resume_after_manual_gate(self) -> bool:
        if self.mode == "resume":
            return True
        run_state = self._load_run_state_safely()
        if run_state is None:
            return False
        return bool(run_state.options.auto_advance)

    def _resolve_resume_auto_advance(self) -> bool:
        if self.resume_auto_advance_override is not None:
            return bool(self.resume_auto_advance_override)
        run_state = self._load_run_state_safely()
        if run_state is None:
            return False
        return bool(run_state.options.auto_advance)

    def _write_heartbeat(
        self,
        *,
        claim_class: str,
        kernel_status: dict[str, Any],
        recovery_decision: RecoveryDecision | None,
        kernel_snapshot: dict[str, Any],
        active_stage: dict[str, Any] | None,
        probe_result: ProbeResult | None,
        rejection_reason: str | None,
    ) -> None:
        observed_at_utc = utc_now_iso()
        payload = {
            "run_id": self.run_id,
            "supervisor_session_id": self.supervisor_session_id,
            "kernel_invocation_id": (
                None if probe_result is None else probe_result.request["kernel_invocation_id"]
            )
            or (None if active_stage is None else active_stage.get("kernel_invocation_id"))
            or (None if self._latest_heartbeat_path is None else None)
            or "",
            "heartbeat_sequence": self._heartbeat_sequence,
            "observed_at_utc": observed_at_utc,
            "claim_class": claim_class,
            "freshness_policy": self.freshness_policy.to_dict(),
            "kernel_snapshot": kernel_snapshot,
            "probe_evidence": None if probe_result is None else probe_result.probe_evidence,
            "active_stage": active_stage,
            "diagnosis_snapshot": diagnosis_snapshot(
                kernel_status_level=kernel_status.get("status_level"),
                pending_action_kind=(kernel_status.get("pending_action") or {}).get("kind"),
                recoverability_class=None if recovery_decision is None else recovery_decision.recoverability_class,
                next_supervisor_action=None if recovery_decision is None else recovery_decision.next_supervisor_action,
            ),
            "rejection_reason": rejection_reason,
            "latest_intervention_path": None
            if self._latest_intervention_path is None
            else repo_relative_path(self.repo_root, self._latest_intervention_path),
        }

        if not payload["kernel_invocation_id"]:
            payload["kernel_invocation_id"] = "none"

        self._latest_heartbeat_path = append_heartbeat(self.paths, payload)
        self._last_heartbeat_observed_at_utc = observed_at_utc
        self._heartbeat_sequence += 1

    def _write_intervention(
        self,
        *,
        action_kind: str,
        recoverability_class: str,
        result_status: str,
        reason: str,
        fingerprint: str | None,
        item_id: str | None,
        attempt_number: int | None,
        terminal_state: str | None,
        command: list[str] | None,
        related_paths: list[str],
        evidence_paths: list[str] | None = None,
        notes: list[str] | None = None,
        evidence_package_sha256: str | None = None,
    ) -> Path:
        self._latest_intervention_path = write_intervention(
            self.paths,
            {
                "run_id": self.run_id,
                "supervisor_session_id": self.supervisor_session_id,
                "intervention_sequence": self._intervention_sequence,
                "observed_at_utc": utc_now_iso(),
                "action_kind": action_kind,
                "item_id": item_id,
                "attempt_number": attempt_number,
                "terminal_state": terminal_state,
                "recoverability_class": recoverability_class,
                "fingerprint": fingerprint,
                "reason": reason,
                "result_status": result_status,
                "command": [] if command is None else list(command),
                "related_paths": related_paths,
                "evidence_paths": [] if evidence_paths is None else evidence_paths,
                "notes": [] if notes is None else notes,
                "evidence_package_sha256": evidence_package_sha256,
            },
        )
        self._intervention_sequence += 1
        return self._latest_intervention_path

    def _probe_once(self, kernel_invocation_id: str) -> ProbeResult:
        if self.paths.probe_ack_path.exists():
            self.paths.probe_ack_path.unlink(missing_ok=True)

        request = {
            "run_id": self.run_id,
            "supervisor_session_id": self.supervisor_session_id,
            "kernel_invocation_id": kernel_invocation_id,
            "probe_sequence": self._probe_sequence,
            "probe_nonce": uuid4().hex,
            "issued_at_utc": utc_now_iso(),
            "expires_at_utc": (
                parse_utc_datetime(utc_now_iso()).replace(microsecond=0)
                if False
                else None
            ),
        }
        issued_at = parse_utc_datetime(request["issued_at_utc"])
        assert issued_at is not None
        request["expires_at_utc"] = (
            issued_at.timestamp() + self.freshness_policy.probe_ack_deadline_sec
        )
        request["expires_at_utc"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(request["expires_at_utc"]),
        )

        write_probe_request(self.paths, request)
        deadline = time.monotonic() + self.freshness_policy.probe_ack_deadline_sec
        ack = None
        while time.monotonic() < deadline:
            ack = load_probe_ack(self.paths)
            if ack is not None:
                if (
                    ack.get("probe_sequence") == request["probe_sequence"]
                    and ack.get("probe_nonce") == request["probe_nonce"]
                    and ack.get("kernel_invocation_id") == request["kernel_invocation_id"]
                ):
                    break
            time.sleep(0.1)
        else:
            ack = None

        valid, rejection_reason, kernel_snapshot, active_stage = validate_probe_roundtrip(
            repo_root=self.repo_root,
            paths=self.paths,
            request=request,
            ack=ack,
            previous_observed_at_utc=self._last_heartbeat_observed_at_utc,
        )
        probe_evidence = build_probe_evidence(
            repo_root=self.repo_root,
            paths=self.paths,
            request=request,
            ack=ack,
        )
        self._probe_sequence += 1
        return ProbeResult(
            request=request,
            ack=ack,
            valid=valid,
            rejection_reason=rejection_reason,
            kernel_snapshot=kernel_snapshot,
            active_stage=active_stage,
            probe_evidence=probe_evidence,
        )

    def _monitor_kernel_invocation(self, invocation: KernelInvocation) -> None:
        while True:
            alive = invocation.process.poll() is None if invocation.process is not None else _pid_is_running(invocation.pid)
            if not alive:
                break

            probe_result = self._probe_once(invocation.kernel_invocation_id)
            kernel_status = load_run_status(self.repo_root, self.run_id)

            self._write_heartbeat(
                claim_class="live_attached" if probe_result.valid else "attachment_unproven",
                kernel_status=kernel_status,
                recovery_decision=None,
                kernel_snapshot=probe_result.kernel_snapshot,
                active_stage=probe_result.active_stage,
                probe_result=probe_result,
                rejection_reason=probe_result.rejection_reason,
            )

            time.sleep(self.freshness_policy.live_probe_interval_sec)

        if invocation.process is not None:
            invocation.process.wait()

    def _result(self, outcome: str) -> dict[str, Any]:
        status = build_supervision_status(self.repo_root, self.run_id)
        return {
            "run_id": self.run_id,
            "supervisor_session_id": self.supervisor_session_id,
            "outcome": outcome,
            "supervision_root": repo_relative_path(self.repo_root, self.paths.supervision_root),
            "latest_heartbeat_path": status["supervision_status"]["latest_heartbeat_path"],
            "latest_intervention_path": status["supervision_status"]["latest_intervention_path"],
            "status": status,
        }

    def manage(self, initial_invocation: KernelInvocation | None) -> dict[str, Any]:
        current_invocation = initial_invocation
        try:
            while True:
                if current_invocation is not None:
                    self._monitor_kernel_invocation(current_invocation)
                    current_invocation = None

                kernel_status = load_run_status(self.repo_root, self.run_id)
                doctor_report = run_doctor(
                    self.repo_root,
                    run_id=self.run_id,
                    fix_safe=False,
                )

                decision = classify_recovery(
                    repo_root=self.repo_root,
                    run_id=self.run_id,
                    status_summary=kernel_status,
                    doctor_report=doctor_report,
                    evidence_inbox_dir=self.evidence_inbox_dir,
                    explicit_external_evidence_dir=self.explicit_external_evidence_dir,
                    max_auto_resume_attempts=self.max_auto_resume_attempts,
                    prior_wait_action_kind=self._prior_wait_action_kind,
                    initial_resume_requested=(self.mode == "resume" and not self._initial_resume_consumed),
                    allow_resume_after_manual_gate=self._allow_resume_after_manual_gate(),
                )

                kernel_snapshot = kernel_snapshot_from_run_state(
                    self.repo_root,
                    self.paths.run_root / "run_state.json",
                )

                if decision.action_kind == "terminal_passed":
                    self._write_heartbeat(
                        claim_class="terminal_observed",
                        kernel_status=kernel_status,
                        recovery_decision=decision,
                        kernel_snapshot=kernel_snapshot,
                        active_stage=None,
                        probe_result=None,
                        rejection_reason=None,
                    )
                    return self._result("passed")

                if decision.action_kind in {"wait_manual_gate", "wait_external_evidence"}:
                    self._prior_wait_action_kind = decision.action_kind
                    self._write_heartbeat(
                        claim_class="waiting_state_observed",
                        kernel_status=kernel_status,
                        recovery_decision=decision,
                        kernel_snapshot=kernel_snapshot,
                        active_stage=None,
                        probe_result=None,
                        rejection_reason=None,
                    )
                    if self._wait_expired():
                        return self._result("waiting_timeout")
                    time.sleep(self.freshness_policy.waiting_poll_interval_sec)
                    continue

                self._prior_wait_action_kind = None
                self._write_heartbeat(
                    claim_class="snapshot_only",
                    kernel_status=kernel_status,
                    recovery_decision=decision,
                    kernel_snapshot=kernel_snapshot,
                    active_stage=None,
                    probe_result=None,
                    rejection_reason=None,
                )

                if decision.action_kind == "doctor_fix_safe":
                    repair_report = run_doctor(
                        self.repo_root,
                        run_id=self.run_id,
                        fix_safe=True,
                    )
                    applied = any(repair.get("status") == "applied" for repair in repair_report.get("repairs", []))
                    related_paths = [
                        repair["path"]
                        for repair in repair_report.get("repairs", [])
                        if repair.get("path")
                    ]
                    self._write_intervention(
                        action_kind="doctor_fix_safe",
                        recoverability_class=decision.recoverability_class,
                        result_status="applied" if applied else "skipped",
                        reason=decision.reason,
                        fingerprint=decision.fingerprint,
                        item_id=decision.item_id,
                        attempt_number=decision.attempt_number,
                        terminal_state=decision.terminal_state,
                        command=[
                            sys.executable,
                            "automation/run_plan_orchestrator.py",
                            "doctor",
                            "--run-id",
                            self.run_id,
                            "--fix-safe",
                            "--format",
                            "json",
                        ],
                        related_paths=related_paths,
                    )
                    continue

                if decision.action_kind in {
                    "resume_saved_run",
                    "resume_after_manual_gate",
                    "resume_blocked_external",
                    "resume_escalated",
                }:
                    resume_auto_advance = self._resolve_resume_auto_advance()
                    current_invocation = self.spawn_supervised_resume(
                        external_evidence_dir=decision.evidence_directory,
                        auto_advance=resume_auto_advance,
                    )
                    self._write_intervention(
                        action_kind=decision.action_kind,
                        recoverability_class=decision.recoverability_class,
                        result_status="applied",
                        reason=decision.reason,
                        fingerprint=decision.fingerprint,
                        item_id=decision.item_id,
                        attempt_number=decision.attempt_number,
                        terminal_state=decision.terminal_state,
                        command=current_invocation.command,
                        related_paths=[
                            repo_relative_path(self.repo_root, current_invocation.stdout_path)
                            if current_invocation.stdout_path is not None
                            else "",
                            repo_relative_path(self.repo_root, current_invocation.stderr_path)
                            if current_invocation.stderr_path is not None
                            else "",
                        ],
                        evidence_paths=[] if decision.evidence_directory is None else [decision.evidence_directory],
                        evidence_package_sha256=decision.evidence_package_sha256,
                    )
                    self.explicit_external_evidence_dir = None
                    continue

                if decision.action_kind == "park":
                    self._write_intervention(
                        action_kind="park",
                        recoverability_class=decision.recoverability_class,
                        result_status="parked",
                        reason=decision.reason,
                        fingerprint=decision.fingerprint,
                        item_id=decision.item_id,
                        attempt_number=decision.attempt_number,
                        terminal_state=decision.terminal_state,
                        command=None,
                        related_paths=[],
                        evidence_paths=[] if decision.evidence_directory is None else [decision.evidence_directory],
                        evidence_package_sha256=decision.evidence_package_sha256,
                    )
                    self._write_heartbeat(
                        claim_class="terminal_observed",
                        kernel_status=kernel_status,
                        recovery_decision=decision,
                        kernel_snapshot=kernel_snapshot,
                        active_stage=None,
                        probe_result=None,
                        rejection_reason=None,
                    )
                    return self._result("parked")

                raise SupervisorError(f"Unknown recovery action: {decision.action_kind}")
        finally:
            self.release_control_lock()


def supervise_run(
    *,
    repo_root: Path,
    playbook_path: str,
    item_id: str | None,
    item_ids: list[str] | None,
    next_only: bool,
    config_path: str | None,
    external_evidence_dir: str | None,
    auto_advance: bool | None,
    max_items: int | None,
    evidence_inbox_dir: str | None,
    freshness_policy: FreshnessPolicy,
    max_auto_resume_attempts: int | None,
    max_wait_seconds: int | None,
) -> dict[str, Any]:
    run_id = make_run_id(allow_override=False)
    supervisor = RunSupervisor(
        repo_root=repo_root,
        run_id=run_id,
        supervisor_session_id=f"svs_{uuid4().hex}",
        mode="run",
        evidence_inbox_dir=evidence_inbox_dir,
        explicit_external_evidence_dir=None,
        resume_auto_advance_override=None,
        freshness_policy=freshness_policy,
        max_auto_resume_attempts=max_auto_resume_attempts,
        max_wait_seconds=max_wait_seconds,
    )
    supervisor.acquire_control_lock()
    initial_invocation = supervisor.spawn_supervised_run(
        playbook_path=playbook_path,
        item_id=item_id,
        item_ids=item_ids,
        next_only=next_only,
        config_path=config_path,
        external_evidence_dir=external_evidence_dir,
        auto_advance=auto_advance,
        max_items=max_items,
    )
    return supervisor.manage(initial_invocation)


def supervise_resume(
    *,
    repo_root: Path,
    run_id: str,
    external_evidence_dir: str | None,
    auto_advance: bool,
    evidence_inbox_dir: str | None,
    freshness_policy: FreshnessPolicy,
    max_auto_resume_attempts: int | None,
    max_wait_seconds: int | None,
) -> dict[str, Any]:
    resolved_external_evidence_dir = None
    if external_evidence_dir is not None:
        candidate = resolve_repo_path(repo_root, external_evidence_dir)
        if not candidate.exists() or not candidate.is_dir():
            raise SupervisorError(f"External evidence directory does not exist: {external_evidence_dir}")
        resolved_external_evidence_dir = candidate.as_posix()

    paths = resolve_supervision_paths(repo_root, run_id)
    bridge_registration = load_bridge_registration(paths)
    supervisor_session_id = (
        str(bridge_registration["supervisor_session_id"])
        if bridge_registration is not None
        else f"svs_{uuid4().hex}"
    )

    supervisor = RunSupervisor(
        repo_root=repo_root,
        run_id=run_id,
        supervisor_session_id=supervisor_session_id,
        mode="resume",
        evidence_inbox_dir=evidence_inbox_dir,
        explicit_external_evidence_dir=resolved_external_evidence_dir,
        resume_auto_advance_override=bool(auto_advance),
        freshness_policy=freshness_policy,
        max_auto_resume_attempts=max_auto_resume_attempts,
        max_wait_seconds=max_wait_seconds,
    )
    supervisor.acquire_control_lock()

    attached = supervisor.attach_existing_live_kernel()
    if attached is not None:
        return supervisor.manage(attached)
    return supervisor.manage(None)
