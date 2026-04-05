from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.plan_orchestrator.config import resolve_run_directories
from automation.plan_orchestrator.state_store import create_run_state, save_run_state
from automation.plan_orchestrator.supervision_artifacts import FreshnessPolicy
from automation.plan_orchestrator.supervisor import supervise_resume
from automation.plan_orchestrator.tests.support import make_item, make_options, make_plan, write_minimal_playbook
from automation.plan_orchestrator.validators import load_json, write_json_atomic


def _doctor_report() -> dict:
    return {
        "checks": [
            {
                "name": "run_references",
                "status": "ok",
                "checks": {
                    "normalized_plan_path_exists": True,
                    "normalized_plan_valid": True,
                    "run_branch_exists": True,
                },
            }
        ]
    }


class SupervisionSmokeTests(unittest.TestCase):
    def test_supervise_resume_waiting_manual_gate_writes_waiting_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_SUPERVISION_SMOKE"
            dirs = resolve_run_directories(repo_root, run_id)
            playbook_path = repo_root / "playbook.md"
            plan = make_plan(items=[make_item("01", 1, manual_gate_required=True)])
            normalized_plan_path = dirs.run_root / "normalized_plan.json"

            write_minimal_playbook(playbook_path)
            write_json_atomic(normalized_plan_path, plan.to_dict())

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path=playbook_path.relative_to(repo_root).as_posix(),
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha="deadbeef",
                run_branch_name=f"orchestrator/run/{run_id}",
                options=make_options(),
                plan=plan,
            )
            run_state.current_state = "ST110_AWAITING_HUMAN_GATE"
            run_state.current_item_id = "01"

            item_state = run_state.get_item_state("01")
            item_state.state = "ST110_AWAITING_HUMAN_GATE"
            item_state.terminal_state = "awaiting_human_gate"
            item_state.manual_gate_status = "pending"

            manual_gate_path = dirs.item_control_dir("01", 1) / "manual_gate.json"
            write_json_atomic(manual_gate_path, {"status": "pending"})
            item_state.latest_paths.manual_gate_path = manual_gate_path.relative_to(repo_root).as_posix()
            save_run_state(dirs.run_state_path, run_state)

            with mock.patch(
                "automation.plan_orchestrator.supervisor.run_doctor",
                return_value=_doctor_report(),
            ):
                result = supervise_resume(
                    repo_root=repo_root,
                    run_id=run_id,
                    external_evidence_dir=None,
                    auto_advance=False,
                    evidence_inbox_dir=None,
                    freshness_policy=FreshnessPolicy(waiting_poll_interval_sec=1),
                    max_auto_resume_attempts=None,
                    max_wait_seconds=0,
                )

            self.assertEqual(result["outcome"], "waiting_timeout")
            self.assertEqual(result["status"]["supervision_status"]["claim_class"], "waiting_state_observed")
            self.assertEqual(result["status"]["supervision_status"]["exit_code"], 10)
            self.assertIsNotNone(result["latest_heartbeat_path"])
            self.assertIsNone(result["latest_intervention_path"])

            heartbeat_path = repo_root / result["latest_heartbeat_path"]
            heartbeat = load_json(heartbeat_path)
            self.assertEqual(heartbeat["claim_class"], "waiting_state_observed")
            self.assertEqual(heartbeat["kernel_snapshot"]["current_state"], "ST110_AWAITING_HUMAN_GATE")
            self.assertEqual(heartbeat["diagnosis_snapshot"]["next_supervisor_action"], "wait")
            self.assertFalse((dirs.run_root / "supervision" / "control.lock").exists())
