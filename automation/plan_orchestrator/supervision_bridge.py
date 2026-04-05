from __future__ import annotations

import os
import subprocess
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .supervision_artifacts import (
    active_stage_snapshot,
    ensure_supervision_layout,
    kernel_snapshot_from_run_state,
    load_probe_request,
    parse_utc_datetime,
    resolve_supervision_paths,
    write_active_stage,
    write_bridge_registration,
    write_probe_ack,
)
from .validators import utc_now_iso

SUPERVISION_ENABLED_ENV = "PLAN_ORCHESTRATOR_SUPERVISION_ENABLED"
SUPERVISOR_SESSION_ID_ENV = "PLAN_ORCHESTRATOR_SUPERVISOR_SESSION_ID"
KERNEL_INVOCATION_ID_ENV = "PLAN_ORCHESTRATOR_KERNEL_INVOCATION_ID"

_CURRENT_BRIDGE: ContextVar["RuntimeProbeBridge | None"] = ContextVar(
    "plan_orchestrator_current_bridge",
    default=None,
)


def supervision_enabled_from_env() -> bool:
    return os.environ.get(SUPERVISION_ENABLED_ENV) == "1"


def current_bridge() -> "RuntimeProbeBridge | None":
    return _CURRENT_BRIDGE.get()


class RuntimeProbeBridge:
    def __init__(
        self,
        *,
        repo_root: Path,
        run_id: str,
        supervisor_session_id: str,
        kernel_invocation_id: str,
    ) -> None:
        self.repo_root = repo_root
        self.run_id = run_id
        self.supervisor_session_id = supervisor_session_id
        self.kernel_invocation_id = kernel_invocation_id
        self.paths = ensure_supervision_layout(resolve_supervision_paths(repo_root, run_id))
        self.run_state_path = self.paths.run_root / "run_state.json"
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._serve_loop,
            name=f"plan-orchestrator-bridge-{run_id}",
            daemon=True,
        )
        self._last_ack_key: tuple[int, str] | None = None

    def start(self) -> None:
        self._safe_unlink(self.paths.probe_request_path)
        self._safe_unlink(self.paths.probe_ack_path)
        self._safe_unlink(self.paths.active_stage_path)
        write_bridge_registration(
            self.paths,
            {
                "run_id": self.run_id,
                "supervisor_session_id": self.supervisor_session_id,
                "kernel_invocation_id": self.kernel_invocation_id,
                "bridge_pid": os.getpid(),
                "bridge_started_at_utc": utc_now_iso(),
                "run_state_path": self.run_state_path.relative_to(self.repo_root).as_posix(),
            },
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._safe_unlink(self.paths.active_stage_path)
        self._safe_unlink(self.paths.probe_ack_path)
        self._safe_unlink(self.paths.probe_request_path)
        self._safe_unlink(self.paths.bridge_registration_path)

    def publish_active_stage(
        self,
        *,
        stage_name: str,
        item_id: str,
        attempt_number: int,
        child_tool: str,
        child_pid: int,
        started_at_utc: str,
        child_command: str | None,
    ) -> None:
        write_active_stage(
            self.paths,
            {
                "run_id": self.run_id,
                "supervisor_session_id": self.supervisor_session_id,
                "kernel_invocation_id": self.kernel_invocation_id,
                "stage_name": stage_name,
                "item_id": item_id,
                "attempt_number": int(attempt_number),
                "child_tool": child_tool,
                "child_pid": int(child_pid),
                "child_command": child_command,
                "started_at_utc": started_at_utc,
                "updated_at_utc": utc_now_iso(),
            },
        )

    def clear_active_stage(self) -> None:
        self._safe_unlink(self.paths.active_stage_path)

    def _serve_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                request = load_probe_request(self.paths)
            except Exception:
                request = None

            if request is None:
                time.sleep(0.1)
                continue

            if request.get("run_id") != self.run_id:
                time.sleep(0.1)
                continue
            if request.get("supervisor_session_id") != self.supervisor_session_id:
                time.sleep(0.1)
                continue
            if request.get("kernel_invocation_id") != self.kernel_invocation_id:
                time.sleep(0.1)
                continue

            ack_key = (int(request["probe_sequence"]), str(request["probe_nonce"]))
            if ack_key == self._last_ack_key:
                time.sleep(0.1)
                continue

            expires_at = parse_utc_datetime(request.get("expires_at_utc"))
            now = datetime.now(timezone.utc)
            if expires_at is not None and now > expires_at:
                self._last_ack_key = ack_key
                time.sleep(0.1)
                continue

            if not self.run_state_path.exists():
                time.sleep(0.1)
                continue

            try:
                kernel_snapshot = kernel_snapshot_from_run_state(self.repo_root, self.run_state_path)
                active_stage = active_stage_snapshot(self.repo_root, self.paths.active_stage_path)
            except Exception:
                time.sleep(0.1)
                continue

            write_probe_ack(
                self.paths,
                {
                    "run_id": self.run_id,
                    "supervisor_session_id": self.supervisor_session_id,
                    "kernel_invocation_id": self.kernel_invocation_id,
                    "probe_sequence": int(request["probe_sequence"]),
                    "probe_nonce": str(request["probe_nonce"]),
                    "acked_at_utc": utc_now_iso(),
                    "kernel_snapshot": {
                        "run_state_path": kernel_snapshot["run_state_path"],
                        "run_state_sha256": kernel_snapshot["run_state_sha256"],
                        "current_state": kernel_snapshot["current_state"],
                        "current_item_id": kernel_snapshot["current_item_id"],
                        "active_stage_present": active_stage is not None,
                        "active_stage_path": None if active_stage is None else active_stage["active_stage_path"],
                        "active_stage_sha256": None if active_stage is None else active_stage["active_stage_sha256"],
                        "run_state_updated_at_utc": kernel_snapshot["run_state_updated_at_utc"],
                    },
                },
            )
            self._last_ack_key = ack_key
            time.sleep(0.1)

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return


@contextmanager
def kernel_supervision_bridge(repo_root: Path, run_id: str | None) -> Iterator[RuntimeProbeBridge | None]:
    if not supervision_enabled_from_env() or not run_id:
        yield None
        return

    supervisor_session_id = os.environ.get(SUPERVISOR_SESSION_ID_ENV, "").strip()
    kernel_invocation_id = os.environ.get(KERNEL_INVOCATION_ID_ENV, "").strip()
    if not supervisor_session_id or not kernel_invocation_id:
        yield None
        return

    bridge: RuntimeProbeBridge | None = None
    try:
        bridge = RuntimeProbeBridge(
            repo_root=repo_root,
            run_id=run_id,
            supervisor_session_id=supervisor_session_id,
            kernel_invocation_id=kernel_invocation_id,
        )
        bridge.start()
    except Exception:
        bridge = None

    token = _CURRENT_BRIDGE.set(bridge)
    try:
        yield bridge
    finally:
        _CURRENT_BRIDGE.reset(token)
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:
                pass


def run_with_active_stage(
    *,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_sec: int,
    stdin_text: str | None,
    stdout_handle,
    stderr_handle,
    stage_name: str,
    item_id: str,
    attempt_number: int,
    child_tool: str,
    child_command: str | None,
) -> int:
    bridge = current_bridge()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        text=True,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=stdout_handle,
        stderr=stderr_handle,
    )
    started_at_utc = utc_now_iso()

    if bridge is not None:
        try:
            bridge.publish_active_stage(
                stage_name=stage_name,
                item_id=item_id,
                attempt_number=attempt_number,
                child_tool=child_tool,
                child_pid=process.pid,
                started_at_utc=started_at_utc,
                child_command=child_command,
            )
        except Exception:
            pass

    try:
        process.communicate(input=stdin_text, timeout=timeout_sec)
        return int(process.returncode or 0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    finally:
        if bridge is not None:
            try:
                bridge.clear_active_stage()
            except Exception:
                pass
