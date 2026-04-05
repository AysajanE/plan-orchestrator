from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.plan_orchestrator.state_store import create_run_state, save_run_state
from automation.plan_orchestrator.supervision_artifacts import (
    resolve_supervision_paths,
    write_intervention,
)
from automation.plan_orchestrator.supervision_recovery import classify_recovery
from automation.plan_orchestrator.tests.support import make_item, make_options, make_plan
from automation.plan_orchestrator.validators import write_json_atomic


def _doctor_report(
    *,
    normalized_plan_path_exists: bool = True,
    normalized_plan_valid: bool = True,
    run_branch_exists: bool = True,
) -> dict:
    return {
        "checks": [
            {
                "name": "run_references",
                "status": "ok",
                "checks": {
                    "normalized_plan_path_exists": normalized_plan_path_exists,
                    "normalized_plan_valid": normalized_plan_valid,
                    "run_branch_exists": run_branch_exists,
                },
            }
        ]
    }


class SupervisionRecoveryTests(unittest.TestCase):
    def _write_run_state(
        self,
        repo_root: Path,
        run_id: str,
        *,
        current_state: str,
        terminal_state: str,
        items=None,
        current_item_id: str = "01",
        auto_advance: bool = False,
    ):
        plan = make_plan(items=items)
        options = make_options()
        options.auto_advance = auto_advance
        paths = resolve_supervision_paths(repo_root, run_id)
        run_state = create_run_state(
            run_id=run_id,
            adapter_id="markdown_playbook_v1",
            repo_root=repo_root.as_posix(),
            playbook_source_path="playbook.md",
            playbook_source_sha256="a" * 64,
            normalized_plan_path="normalized_plan.json",
            base_head_sha="deadbeef",
            run_branch_name=f"orchestrator/run/{run_id}",
            options=options,
            plan=plan,
        )
        run_state.current_state = current_state
        run_state.current_item_id = current_item_id
        item_state = run_state.get_item_state(current_item_id)
        item_state.state = current_state
        item_state.terminal_state = terminal_state
        save_run_state(paths.run_root / "run_state.json", run_state)
        return paths, run_state, item_state

    def _record_intervention(self, paths, *, action_kind: str, fingerprint: str | None, sequence: int) -> None:
        write_intervention(
            paths,
            {
                "run_id": paths.run_root.name,
                "supervisor_session_id": "svs_test",
                "intervention_sequence": sequence,
                "observed_at_utc": "2026-04-05T11:00:00Z",
                "action_kind": action_kind,
                "item_id": "01",
                "attempt_number": 1,
                "terminal_state": "blocked_external" if "external" in action_kind else "escalated",
                "recoverability_class": "recoverable",
                "fingerprint": fingerprint,
                "reason": "prior attempt",
                "result_status": "applied",
                "command": ["python", "automation/run_plan_orchestrator.py", "resume"],
                "related_paths": [],
                "evidence_paths": [],
                "notes": [],
                "evidence_package_sha256": None,
            },
        )

    def test_manual_gate_waits_for_human_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            _paths, _run_state, _item_state = self._write_run_state(
                repo_root,
                "RUN_WAIT_MANUAL_GATE",
                current_state="ST110_AWAITING_HUMAN_GATE",
                terminal_state="awaiting_human_gate",
                items=[make_item("01", 1, manual_gate_required=True)],
            )

            decision = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_WAIT_MANUAL_GATE",
                status_summary={"pending_action": {"kind": "manual_gate", "detail": "Manual gate decision required."}},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=None,
                max_auto_resume_attempts=None,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )

            self.assertEqual(decision.action_kind, "wait_manual_gate")
            self.assertEqual(decision.recoverability_class, "waiting")
            self.assertEqual(decision.next_supervisor_action, "wait")

    def test_manual_gate_approval_can_resume_after_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            items = [make_item("01", 1, manual_gate_required=True), make_item("02", 2)]
            _paths, run_state, item_state = self._write_run_state(
                repo_root,
                "RUN_RESUME_AFTER_GATE",
                current_state="ST130_PASSED",
                terminal_state="passed",
                items=items,
                auto_advance=True,
            )
            item_state.manual_gate_status = "approved"
            run_state.get_item_state("02").terminal_state = "none"
            save_run_state(resolve_supervision_paths(repo_root, "RUN_RESUME_AFTER_GATE").run_root / "run_state.json", run_state)

            decision = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_RESUME_AFTER_GATE",
                status_summary={"pending_action": None},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=None,
                max_auto_resume_attempts=None,
                prior_wait_action_kind="wait_manual_gate",
                initial_resume_requested=False,
                allow_resume_after_manual_gate=True,
            )

            self.assertEqual(decision.action_kind, "resume_after_manual_gate")
            self.assertEqual(decision.next_supervisor_action, "resume")

    def test_blocked_external_resumes_when_local_evidence_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            evidence_dir = repo_root / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / "provider_status_snapshot.md").write_text("ready\n", encoding="utf-8")
            _paths, _run_state, _item_state = self._write_run_state(
                repo_root,
                "RUN_BLOCKED_EXTERNAL",
                current_state="ST120_BLOCKED_EXTERNAL",
                terminal_state="blocked_external",
                items=[make_item("01", 1, external_check_required=True)],
            )

            decision = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_BLOCKED_EXTERNAL",
                status_summary={"pending_action": {"kind": "external_evidence", "detail": "Still waiting for evidence."}},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=evidence_dir.as_posix(),
                max_auto_resume_attempts=None,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )

            self.assertEqual(decision.action_kind, "resume_blocked_external")
            self.assertEqual(decision.next_supervisor_action, "resume")
            self.assertEqual(decision.evidence_directory, evidence_dir.as_posix())
            self.assertIsNotNone(decision.evidence_package_sha256)

    def test_blocked_external_parks_after_retrying_same_evidence_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            evidence_dir = repo_root / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / "provider_status_snapshot.md").write_text("ready\n", encoding="utf-8")
            paths, _run_state, _item_state = self._write_run_state(
                repo_root,
                "RUN_BLOCKED_RETRY",
                current_state="ST120_BLOCKED_EXTERNAL",
                terminal_state="blocked_external",
                items=[make_item("01", 1, external_check_required=True)],
            )

            first = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_BLOCKED_RETRY",
                status_summary={"pending_action": {"kind": "external_evidence", "detail": "Still waiting for evidence."}},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=evidence_dir.as_posix(),
                max_auto_resume_attempts=None,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )
            self._record_intervention(
                paths,
                action_kind="resume_blocked_external",
                fingerprint=first.fingerprint,
                sequence=1,
            )

            second = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_BLOCKED_RETRY",
                status_summary={"pending_action": {"kind": "external_evidence", "detail": "Still waiting for evidence."}},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=evidence_dir.as_posix(),
                max_auto_resume_attempts=None,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )

            self.assertEqual(second.action_kind, "park")
            self.assertEqual(second.next_supervisor_action, "park")

    def test_repairable_local_drift_prefers_doctor_fix_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._write_run_state(
                repo_root,
                "RUN_DOCTOR_FIX",
                current_state="ST30_EXECUTING",
                terminal_state="none",
            )

            decision = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_DOCTOR_FIX",
                status_summary={"pending_action": {"kind": "repair_local_state", "detail": "Missing normalized plan."}},
                doctor_report=_doctor_report(normalized_plan_path_exists=False, normalized_plan_valid=False),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=None,
                max_auto_resume_attempts=None,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )

            self.assertEqual(decision.action_kind, "doctor_fix_safe")
            self.assertEqual(decision.next_supervisor_action, "doctor_fix_safe")

    def test_manual_gate_rejection_escalation_is_parked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            paths, run_state, item_state = self._write_run_state(
                repo_root,
                "RUN_ESCALATED_REJECTED",
                current_state="ST140_ESCALATED",
                terminal_state="escalated",
                items=[make_item("01", 1, manual_gate_required=True)],
            )
            manual_gate_path = paths.run_root / "items" / "01" / "attempt-1" / "manual_gate.json"
            write_json_atomic(manual_gate_path, {"status": "rejected"})
            item_state.latest_paths.manual_gate_path = manual_gate_path.relative_to(repo_root).as_posix()
            save_run_state(paths.run_root / "run_state.json", run_state)

            decision = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_ESCALATED_REJECTED",
                status_summary={"pending_action": {"kind": "escalated", "detail": "Escalated after rejection."}},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=None,
                max_auto_resume_attempts=None,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )

            self.assertEqual(decision.action_kind, "park")
            self.assertEqual(decision.recoverability_class, "non_recoverable")
            self.assertIn("manual-gate rejection", decision.reason)

    def test_escalated_budget_is_bounded_by_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            paths, _run_state, _item_state = self._write_run_state(
                repo_root,
                "RUN_ESCALATED_BUDGET",
                current_state="ST140_ESCALATED",
                terminal_state="escalated",
            )

            first = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_ESCALATED_BUDGET",
                status_summary={"pending_action": {"kind": "escalated", "detail": "Recoverable escalation."}},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=None,
                max_auto_resume_attempts=2,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )
            self.assertEqual(first.action_kind, "resume_escalated")

            self._record_intervention(paths, action_kind="resume_escalated", fingerprint=first.fingerprint, sequence=1)
            self._record_intervention(paths, action_kind="resume_escalated", fingerprint=first.fingerprint, sequence=2)

            second = classify_recovery(
                repo_root=repo_root,
                run_id="RUN_ESCALATED_BUDGET",
                status_summary={"pending_action": {"kind": "escalated", "detail": "Recoverable escalation."}},
                doctor_report=_doctor_report(),
                evidence_inbox_dir=None,
                explicit_external_evidence_dir=None,
                max_auto_resume_attempts=2,
                prior_wait_action_kind=None,
                initial_resume_requested=False,
                allow_resume_after_manual_gate=False,
            )

            self.assertEqual(second.action_kind, "park")
            self.assertEqual(second.next_supervisor_action, "park")
