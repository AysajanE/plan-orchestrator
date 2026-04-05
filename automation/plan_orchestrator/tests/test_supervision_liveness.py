from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.plan_orchestrator.models import NormalizedPlan, PlanItem, RuntimeOptions
from automation.plan_orchestrator.state_store import create_run_state, save_run_state
from automation.plan_orchestrator.supervision_artifacts import (
    FreshnessPolicy,
    append_heartbeat,
    build_probe_evidence,
    diagnosis_snapshot,
    kernel_snapshot_from_run_state,
    resolve_supervision_paths,
    write_active_stage,
    write_bridge_registration,
    write_probe_ack,
    write_probe_request,
)
from automation.plan_orchestrator.supervision_bridge import RuntimeProbeBridge
from automation.plan_orchestrator.supervision_status import (
    SUPERVISE_STATUS_EXIT_CODES,
    build_supervision_status,
)
from automation.plan_orchestrator.supervisor import validate_probe_roundtrip
from automation.plan_orchestrator.validators import utc_now_iso


def make_item(item_id: str, order: int) -> PlanItem:
    return PlanItem(
        item_id=item_id,
        order=order,
        phase="phase",
        phase_slug="phase",
        action="do thing",
        why_now="needed now",
        owner_type="operator",
        prerequisites_raw="none",
        prerequisite_item_ids=[],
        repo_surfaces_raw=["docs/runbooks/example.md"],
        repo_surface_paths=["docs/runbooks/example.md"],
        external_dependencies_raw=[],
        deliverable="docs/runbooks/example.md",
        deliverable_paths=["docs/runbooks/example.md"],
        exit_criteria="exists",
        change_profile="docs_only",
        execution_mode="codex",
        host_commands=[],
        requires_red_green=False,
        manual_gate={"required": False, "gate_type": "none", "gate_reason": "", "required_evidence": []},
        external_check={"required": False, "mode": "none", "dependencies": []},
        verification_hints={"required_artifacts": [], "required_commands": [], "suggested_commands": []},
        source_row={
            "section_title": "Ordered Execution Plan",
            "row_index": 1,
            "line_start": 1,
            "line_end": 1,
            "raw_row_markdown": "| row |",
        },
        support_section_ids=[],
        consult_paths=["docs/runbooks/example.md"],
        allowed_write_roots=["docs/runbooks"],
        notes=[],
    )


def make_plan() -> NormalizedPlan:
    return NormalizedPlan(
        schema_version="plan_orchestrator.normalized_plan.v1",
        adapter_id="markdown_playbook_v1",
        plan_source={
            "path": "playbook.md",
            "source_kind": "markdown_playbook_v1",
            "sha256": "a" * 64,
            "title": "Playbook",
        },
        generated_at_utc="2026-04-05T11:00:00Z",
        global_context={
            "primary_goal": "goal",
            "immediate_target": "01",
            "default_runtime_profile": {
                "offline_default": True,
                "auto_advance_default": False,
                "max_fix_rounds_default": 2,
                "max_remediation_rounds_default": 1,
            },
            "global_support_section_ids": [],
            "notes": [],
        },
        support_sections=[],
        items=[make_item("01", 1)],
    )


def make_options() -> RuntimeOptions:
    return RuntimeOptions(
        codex_model="gpt-5.4",
        codex_reasoning_effort="xhigh",
        claude_model="opus",
        claude_effort="max",
        auto_advance=False,
        max_items=None,
        max_fix_rounds=2,
        max_remediation_rounds=1,
        execution_timeout_sec=10,
        verification_timeout_sec=10,
        audit_timeout_sec=10,
        triage_timeout_sec=10,
        fix_timeout_sec=10,
        remediation_timeout_sec=10,
    )


def _iso_at(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SupervisionLivenessTests(unittest.TestCase):
    def _write_run_state(self, repo_root: Path, run_id: str) -> Path:
        paths = resolve_supervision_paths(repo_root, run_id)
        plan = make_plan()
        run_state = create_run_state(
            run_id=run_id,
            adapter_id="markdown_playbook_v1",
            repo_root=repo_root.as_posix(),
            playbook_source_path="playbook.md",
            playbook_source_sha256="a" * 64,
            normalized_plan_path="normalized_plan.json",
            base_head_sha="deadbeef",
            run_branch_name=f"orchestrator/run/{run_id}",
            options=make_options(),
            plan=plan,
        )
        run_state.current_state = "ST30_EXECUTING"
        run_state.current_item_id = "01"
        item_state = run_state.get_item_state("01")
        item_state.state = "ST30_EXECUTING"
        save_run_state(paths.run_root / "run_state.json", run_state)
        return paths.run_root / "run_state.json"

    def _write_probe_roundtrip(
        self,
        *,
        repo_root: Path,
        run_id: str,
        supervisor_session_id: str,
        kernel_invocation_id: str,
        issued_at_utc: str,
        expires_at_utc: str,
        acked_at_utc: str,
    ) -> tuple[dict, dict]:
        paths = resolve_supervision_paths(repo_root, run_id)
        kernel_snapshot = kernel_snapshot_from_run_state(repo_root, paths.run_root / "run_state.json")

        request = {
            "run_id": run_id,
            "supervisor_session_id": supervisor_session_id,
            "kernel_invocation_id": kernel_invocation_id,
            "probe_sequence": 1,
            "probe_nonce": "nonce-1",
            "issued_at_utc": issued_at_utc,
            "expires_at_utc": expires_at_utc,
        }
        write_probe_request(paths, request)

        ack = {
            "run_id": run_id,
            "supervisor_session_id": supervisor_session_id,
            "kernel_invocation_id": kernel_invocation_id,
            "probe_sequence": 1,
            "probe_nonce": "nonce-1",
            "acked_at_utc": acked_at_utc,
            "kernel_snapshot": {
                "run_state_path": kernel_snapshot["run_state_path"],
                "run_state_sha256": kernel_snapshot["run_state_sha256"],
                "current_state": kernel_snapshot["current_state"],
                "current_item_id": kernel_snapshot["current_item_id"],
                "active_stage_present": False,
                "active_stage_path": None,
                "active_stage_sha256": None,
                "run_state_updated_at_utc": kernel_snapshot["run_state_updated_at_utc"],
            },
        }
        write_probe_ack(paths, ack)
        return request, ack

    def test_runtime_probe_bridge_acknowledges_fresh_nonce_and_active_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_LIVE_BRIDGE"
            paths = resolve_supervision_paths(repo_root, run_id)
            self._write_run_state(repo_root, run_id)

            bridge = RuntimeProbeBridge(
                repo_root=repo_root,
                run_id=run_id,
                supervisor_session_id="svs_bridge",
                kernel_invocation_id="kernel_bridge",
            )
            bridge.start()
            try:
                write_active_stage(
                    paths,
                    {
                        "run_id": run_id,
                        "supervisor_session_id": "svs_bridge",
                        "kernel_invocation_id": "kernel_bridge",
                        "stage_name": "execute",
                        "item_id": "01",
                        "attempt_number": 1,
                        "child_tool": "codex",
                        "child_pid": 12345,
                        "child_command": "codex exec ...",
                        "started_at_utc": "2026-04-05T11:00:00Z",
                        "updated_at_utc": "2026-04-05T11:00:00Z",
                    },
                )
                write_probe_request(
                    paths,
                    {
                        "run_id": run_id,
                        "supervisor_session_id": "svs_bridge",
                        "kernel_invocation_id": "kernel_bridge",
                        "probe_sequence": 1,
                        "probe_nonce": "nonce-1",
                        "issued_at_utc": "2026-04-05T11:00:00Z",
                        "expires_at_utc": "2099-04-05T11:00:05Z",
                    },
                )
                for _ in range(50):
                    if paths.probe_ack_path.exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(paths.probe_ack_path.exists())
                ack = paths.probe_ack_path.read_text(encoding="utf-8")
                self.assertIn('"probe_nonce": "nonce-1"', ack)
                self.assertIn('"active_stage_present": true', ack)
            finally:
                bridge.stop()

    def test_validate_probe_roundtrip_rejects_run_state_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_VALIDATE_MISMATCH"
            paths = resolve_supervision_paths(repo_root, run_id)
            self._write_run_state(repo_root, run_id)

            write_bridge_registration(
                paths,
                {
                    "run_id": run_id,
                    "supervisor_session_id": "svs_validate",
                    "kernel_invocation_id": "kernel_validate",
                    "bridge_pid": 9999,
                    "bridge_started_at_utc": "2026-04-05T11:00:00Z",
                    "run_state_path": ".local/automation/plan_orchestrator/runs/RUN_VALIDATE_MISMATCH/run_state.json",
                },
            )
            request = {
                "run_id": run_id,
                "supervisor_session_id": "svs_validate",
                "kernel_invocation_id": "kernel_validate",
                "probe_sequence": 1,
                "probe_nonce": "nonce-1",
                "issued_at_utc": "2026-04-05T11:00:00Z",
                "expires_at_utc": "2099-04-05T11:00:05Z",
            }
            kernel_snapshot = kernel_snapshot_from_run_state(repo_root, paths.run_root / "run_state.json")
            ack = {
                "run_id": run_id,
                "supervisor_session_id": "svs_validate",
                "kernel_invocation_id": "kernel_validate",
                "probe_sequence": 1,
                "probe_nonce": "nonce-1",
                "acked_at_utc": "2026-04-05T11:00:01Z",
                "kernel_snapshot": {
                    "run_state_path": kernel_snapshot["run_state_path"],
                    "run_state_sha256": "f" * 64,
                    "current_state": kernel_snapshot["current_state"],
                    "current_item_id": kernel_snapshot["current_item_id"],
                    "active_stage_present": False,
                    "active_stage_path": None,
                    "active_stage_sha256": None,
                    "run_state_updated_at_utc": kernel_snapshot["run_state_updated_at_utc"],
                },
            }

            valid, rejection_reason, observed_snapshot, active_stage = validate_probe_roundtrip(
                repo_root=repo_root,
                paths=paths,
                request=request,
                ack=ack,
                previous_observed_at_utc=None,
            )

            self.assertFalse(valid)
            self.assertEqual(rejection_reason, "run_state_sha256_mismatch")
            self.assertEqual(observed_snapshot["run_state_sha256"], kernel_snapshot["run_state_sha256"])
            self.assertIsNone(active_stage)

    def test_build_supervision_status_is_snapshot_only_without_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_SNAPSHOT_ONLY"
            self._write_run_state(repo_root, run_id)

            summary = build_supervision_status(repo_root, run_id)

            self.assertEqual(summary["supervision_status"]["claim_class"], "snapshot_only")
            self.assertEqual(
                summary["supervision_status"]["exit_code"],
                SUPERVISE_STATUS_EXIT_CODES["snapshot_only"],
            )
            self.assertIn("No supervision heartbeat ledger exists", summary["supervision_status"]["reason"])

    def test_build_supervision_status_degrades_stale_live_heartbeat_to_attachment_unproven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_STALE_LIVE"
            paths = resolve_supervision_paths(repo_root, run_id)
            run_state_path = self._write_run_state(repo_root, run_id)

            now = datetime.now(timezone.utc)
            observed_at = _iso_at(now - timedelta(minutes=10))
            issued_at = _iso_at(now - timedelta(minutes=10, seconds=1))
            expires_at = _iso_at(now - timedelta(minutes=10) + timedelta(seconds=4))
            acked_at = _iso_at(now - timedelta(minutes=10))

            write_bridge_registration(
                paths,
                {
                    "run_id": run_id,
                    "supervisor_session_id": "svs_stale",
                    "kernel_invocation_id": "kernel_stale",
                    "bridge_pid": 9999,
                    "bridge_started_at_utc": utc_now_iso(),
                    "run_state_path": run_state_path.relative_to(repo_root).as_posix(),
                },
            )
            request, ack = self._write_probe_roundtrip(
                repo_root=repo_root,
                run_id=run_id,
                supervisor_session_id="svs_stale",
                kernel_invocation_id="kernel_stale",
                issued_at_utc=issued_at,
                expires_at_utc=expires_at,
                acked_at_utc=acked_at,
            )

            append_heartbeat(
                paths,
                {
                    "run_id": run_id,
                    "supervisor_session_id": "svs_stale",
                    "kernel_invocation_id": "kernel_stale",
                    "heartbeat_sequence": 1,
                    "observed_at_utc": observed_at,
                    "claim_class": "live_attached",
                    "freshness_policy": FreshnessPolicy().to_dict(),
                    "kernel_snapshot": kernel_snapshot_from_run_state(repo_root, run_state_path),
                    "probe_evidence": build_probe_evidence(
                        repo_root=repo_root,
                        paths=paths,
                        request=request,
                        ack=ack,
                    ),
                    "active_stage": None,
                    "diagnosis_snapshot": diagnosis_snapshot(
                        kernel_status_level="ok",
                        pending_action_kind=None,
                        recoverability_class=None,
                        next_supervisor_action=None,
                    ),
                    "rejection_reason": None,
                    "latest_intervention_path": None,
                },
            )

            summary = build_supervision_status(repo_root, run_id)

            self.assertEqual(summary["supervision_status"]["claim_class"], "attachment_unproven")
            self.assertEqual(
                summary["supervision_status"]["exit_code"],
                SUPERVISE_STATUS_EXIT_CODES["attachment_unproven"],
            )
            self.assertEqual(summary["supervision_status"]["last_heartbeat_claim_class"], "live_attached")

