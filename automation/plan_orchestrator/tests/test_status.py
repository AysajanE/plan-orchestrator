from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.plan_orchestrator.config import RUNTIME_POLICY_FIELD_NAMES, resolve_run_directories
from automation.plan_orchestrator.state_machine import StateId
from automation.plan_orchestrator.state_store import create_run_state, save_run_state
from automation.plan_orchestrator.tests.support import (
    make_item,
    make_options,
    make_plan,
    write_runtime_policy_snapshot,
)
from automation.plan_orchestrator.validators import write_json_atomic
from automation.plan_orchestrator.status import list_run_statuses, load_run_status


class StatusTests(unittest.TestCase):
    def test_load_run_status_reports_waiting_manual_gate_and_latest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_STATUS_WAITING"
            dirs = resolve_run_directories(repo_root, run_id)
            plan = make_plan(items=[make_item("01", 1, manual_gate_required=True)])

            playbook_path = repo_root / "playbook.md"
            playbook_path.write_text("playbook\n", encoding="utf-8")
            normalized_plan_path = dirs.run_root / "normalized_plan.json"
            write_json_atomic(normalized_plan_path, plan.to_dict())

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path="playbook.md",
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha="deadbeef",
                run_branch_name=f"orchestrator/run/{run_id}",
                options=make_options(),
                plan=plan,
            )
            item_state = run_state.get_item_state("01")
            run_state.current_state = StateId.ST110_AWAITING_HUMAN_GATE.value
            run_state.current_item_id = "01"
            item_state.state = StateId.ST110_AWAITING_HUMAN_GATE.value
            item_state.terminal_state = "awaiting_human_gate"
            item_state.manual_gate_status = "pending"
            manual_gate_path = dirs.item_control_dir("01", 1) / "manual_gate.json"
            write_json_atomic(manual_gate_path, {"status": "pending"})
            item_state.latest_paths.manual_gate_path = manual_gate_path.relative_to(repo_root).as_posix()
            save_run_state(dirs.run_state_path, run_state)

            summary = load_run_status(repo_root, run_id)

        self.assertEqual(summary["run_id"], run_id)
        self.assertEqual(summary["status_level"], "waiting")
        self.assertEqual(summary["exit_code"], 1)
        self.assertEqual(summary["pending_action"]["kind"], "manual_gate")
        self.assertEqual(summary["current_item"]["item_id"], "01")
        self.assertEqual(
            summary["current_item"]["latest_paths"]["manual_gate_path"],
            manual_gate_path.relative_to(repo_root).as_posix(),
        )
        self.assertTrue(summary["checks"]["manual_gate_path_exists"])

    def test_load_run_status_reports_escalated_runs_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_STATUS_ESCALATED"
            dirs = resolve_run_directories(repo_root, run_id)
            plan = make_plan()

            playbook_path = repo_root / "playbook.md"
            playbook_path.write_text("playbook\n", encoding="utf-8")
            normalized_plan_path = dirs.run_root / "normalized_plan.json"
            write_json_atomic(normalized_plan_path, plan.to_dict())

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path="playbook.md",
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha="deadbeef",
                run_branch_name=f"orchestrator/run/{run_id}",
                options=make_options(),
                plan=plan,
            )
            item_state = run_state.get_item_state("01")
            run_state.current_state = StateId.ST140_ESCALATED.value
            run_state.current_item_id = "01"
            item_state.state = StateId.ST140_ESCALATED.value
            item_state.terminal_state = "escalated"
            escalation_path = dirs.item_control_dir("01", 1) / "escalation_manifest.json"
            write_json_atomic(escalation_path, {"status": "escalated"})
            item_state.latest_paths.escalation_manifest_path = escalation_path.relative_to(
                repo_root
            ).as_posix()
            save_run_state(dirs.run_state_path, run_state)

            summary = load_run_status(repo_root, run_id)

        self.assertEqual(summary["status_level"], "error")
        self.assertEqual(summary["exit_code"], 2)
        self.assertEqual(summary["pending_action"]["kind"], "escalated")
        self.assertTrue(summary["checks"]["escalation_manifest_path_exists"])

    def test_list_run_statuses_discovers_saved_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            for run_id in ("RUN_STATUS_A", "RUN_STATUS_B"):
                dirs = resolve_run_directories(repo_root, run_id)
                plan = make_plan()
                playbook_path = repo_root / "playbook.md"
                playbook_path.write_text("playbook\n", encoding="utf-8")
                normalized_plan_path = dirs.run_root / "normalized_plan.json"
                write_json_atomic(normalized_plan_path, plan.to_dict())
                run_state = create_run_state(
                    run_id=run_id,
                    adapter_id="markdown_playbook_v1",
                    repo_root=repo_root.as_posix(),
                    playbook_source_path="playbook.md",
                    playbook_source_sha256="a" * 64,
                    normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                    base_head_sha="deadbeef",
                    run_branch_name=f"orchestrator/run/{run_id}",
                    options=make_options(),
                    plan=plan,
                )
                save_run_state(dirs.run_state_path, run_state)

            summaries = list_run_statuses(repo_root)

        self.assertEqual({summary["run_id"] for summary in summaries}, {"RUN_STATUS_A", "RUN_STATUS_B"})

    def test_load_run_status_reports_missing_runtime_policy_snapshot_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_STATUS_POLICY_WARNING"
            dirs = resolve_run_directories(repo_root, run_id)
            plan = make_plan()

            playbook_path = repo_root / "playbook.md"
            playbook_path.write_text("playbook\n", encoding="utf-8")
            normalized_plan_path = dirs.run_root / "normalized_plan.json"
            write_json_atomic(normalized_plan_path, plan.to_dict())
            options = make_options()
            runtime_policy_path = dirs.run_root / "runtime_policy.json"

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path="playbook.md",
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha="deadbeef",
                run_branch_name=f"orchestrator/run/{run_id}",
                options=options,
                plan=plan,
                runtime_policy_path=runtime_policy_path.relative_to(repo_root).as_posix(),
                runtime_policy_sha256="f" * 64,
                runtime_policy_sources={field: "default" for field in RUNTIME_POLICY_FIELD_NAMES},
            )
            save_run_state(dirs.run_state_path, run_state)

            summary = load_run_status(repo_root, run_id)

        self.assertEqual(summary["status_level"], "warning")
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(
            summary["runtime_policy_path"],
            runtime_policy_path.relative_to(repo_root).as_posix(),
        )
        self.assertEqual(summary["runtime_policy_sha256"], "f" * 64)
        self.assertEqual(summary["runtime_policy_sources"]["codex_model"], "default")
        self.assertFalse(summary["checks"]["runtime_policy_path_exists"])
        self.assertIsNone(summary["checks"]["runtime_policy_sha256_matches"])
        self.assertIsNone(summary["checks"]["runtime_policy_matches_run_state"])

    def test_load_run_status_reports_runtime_policy_mismatch_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run_id = "RUN_STATUS_POLICY_MISMATCH"
            dirs = resolve_run_directories(repo_root, run_id)
            plan = make_plan()

            playbook_path = repo_root / "playbook.md"
            playbook_path.write_text("playbook\n", encoding="utf-8")
            normalized_plan_path = dirs.run_root / "normalized_plan.json"
            write_json_atomic(normalized_plan_path, plan.to_dict())
            options = make_options()
            runtime_policy_path = dirs.run_root / "runtime_policy.json"
            runtime_policy_sha256 = write_runtime_policy_snapshot(runtime_policy_path, options)

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path="playbook.md",
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha="deadbeef",
                run_branch_name=f"orchestrator/run/{run_id}",
                options=options,
                plan=plan,
                runtime_policy_path=runtime_policy_path.relative_to(repo_root).as_posix(),
                runtime_policy_sha256=runtime_policy_sha256,
                runtime_policy_sources={field: "default" for field in RUNTIME_POLICY_FIELD_NAMES},
            )
            run_state.options.codex_model = "drifted-from-snapshot"
            save_run_state(dirs.run_state_path, run_state)

            summary = load_run_status(repo_root, run_id)

        self.assertEqual(summary["status_level"], "warning")
        self.assertEqual(summary["exit_code"], 0)
        self.assertTrue(summary["checks"]["runtime_policy_path_exists"])
        self.assertTrue(summary["checks"]["runtime_policy_sha256_matches"])
        self.assertFalse(summary["checks"]["runtime_policy_matches_run_state"])
