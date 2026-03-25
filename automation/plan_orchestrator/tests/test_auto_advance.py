from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation.plan_orchestrator.config import RunDirectories, make_run_id, resolve_run_directories
from automation.plan_orchestrator.models import NormalizedPlan, PlanItem, RuntimeOptions
from automation.plan_orchestrator.reporting import write_manual_gate_record
from automation.plan_orchestrator.runtime import OrchestratorError, PlanOrchestrator
from automation.plan_orchestrator.state_machine import StateId
from automation.plan_orchestrator.state_store import create_run_state, load_run_state, save_run_state


def make_item(
    item_id: str,
    order: int,
    *,
    prereqs: list[str] | None = None,
    manual_gate_required: bool = False,
    external_check_required: bool = False,
) -> PlanItem:
    return PlanItem(
        item_id=item_id,
        order=order,
        phase=f"Phase {item_id}",
        phase_slug=f"phase-{item_id}",
        action="Do the thing.",
        why_now="Needed now.",
        owner_type="operator",
        prerequisites_raw=",".join(prereqs or []) or "none",
        prerequisite_item_ids=list(prereqs or []),
        repo_surfaces_raw=["docs/runbooks/policy_note.md"],
        repo_surface_paths=["docs/runbooks/policy_note.md"],
        external_dependencies_raw=["current provider bundle"] if external_check_required else [],
        deliverable="docs/runbooks/policy_note.md",
        deliverable_paths=["docs/runbooks/policy_note.md"],
        exit_criteria="Artifact exists.",
        change_profile="docs_only",
        execution_mode="codex",
        host_commands=[],
        requires_red_green=False,
        manual_gate={
            "required": manual_gate_required,
            "gate_type": "signoff" if manual_gate_required else "none",
            "gate_reason": "Human signoff required." if manual_gate_required else "",
            "required_evidence": ["signed note"] if manual_gate_required else [],
        },
        external_check={
            "required": external_check_required,
            "mode": "human_supplied_evidence_required" if external_check_required else "none",
            "dependencies": ["current provider bundle"] if external_check_required else [],
        },
        verification_hints={
            "required_artifacts": ["docs/runbooks/policy_note.md"],
            "required_commands": [],
            "suggested_commands": [],
        },
        source_row={
            "section_title": "Ordered Execution Plan",
            "row_index": order,
            "line_start": order + 10,
            "line_end": order + 10,
            "raw_row_markdown": f"| {item_id} | row |",
        },
        support_section_ids=[],
        consult_paths=["docs/runbooks/policy_note.md"],
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
            "title": "Fixture Playbook",
        },
        generated_at_utc="2026-03-25T12:00:00Z",
        global_context={
            "primary_goal": "Goal.",
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
        items=[
            make_item("01", 1),
            make_item("02", 2, prereqs=["01"]),
            make_item("03", 3, prereqs=["02"]),
        ],
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
        execution_timeout_sec=1,
        verification_timeout_sec=1,
        audit_timeout_sec=1,
        triage_timeout_sec=1,
        fix_timeout_sec=1,
        remediation_timeout_sec=1,
    )


def init_git_repo(repo_root: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo_root, check=True)


def git_commit_all(repo_root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True, capture_output=True, text=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class AutoAdvanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = make_plan()
        self.run_state = create_run_state(
            run_id="RUN_TEST",
            adapter_id="markdown_playbook_v1",
            repo_root=".",
            playbook_source_path="playbook.md",
            playbook_source_sha256="a" * 64,
            normalized_plan_path="normalized_plan.json",
            base_head_sha="deadbeef",
            run_branch_name="orchestrator/run/RUN_TEST",
            options=make_options(),
            plan=self.plan,
        )
        self.orchestrator = PlanOrchestrator(Path(".").resolve())

    def _mark_item_passed(self, run_state, item_id: str) -> None:
        item_state = run_state.get_item_state(item_id)
        item_state.state = StateId.ST130_PASSED.value
        item_state.terminal_state = "passed"
        run_state.current_state = StateId.ST130_PASSED.value
        run_state.current_item_id = item_id

    def test_make_run_id_adds_uniqueness_suffix_for_same_second(self) -> None:
        frozen = datetime(2026, 3, 25, 12, 0, 0)

        with mock.patch(
            "automation.plan_orchestrator.config.uuid4",
            side_effect=[
                SimpleNamespace(hex="11111111222222223333333344444444"),
                SimpleNamespace(hex="aaaaaaaa55555555bbbbbbbb66666666"),
            ],
            create=True,
        ):
            first = make_run_id(frozen)
            second = make_run_id(frozen)

        self.assertEqual(first, "RUN_20260325T120000Z_11111111222222223333333344444444")
        self.assertEqual(second, "RUN_20260325T120000Z_aaaaaaaa55555555bbbbbbbb66666666")
        self.assertNotEqual(first, second)

    def test_resolve_requested_items_preserves_explicit_order(self) -> None:
        resolved = self.orchestrator._resolve_requested_items(
            plan=self.plan,
            run_state=self.run_state,
            explicit_item=None,
            explicit_items=["03", "01"],
            next_only=False,
        )

        self.assertEqual(resolved, ["03", "01"])

    def test_next_returns_first_unfinished_item(self) -> None:
        self.run_state.get_item_state("01").terminal_state = "passed"
        self.run_state.get_item_state("02").terminal_state = "passed"

        resolved = self.orchestrator._resolve_requested_items(
            plan=self.plan,
            run_state=self.run_state,
            explicit_item=None,
            explicit_items=[],
            next_only=True,
        )

        self.assertEqual(resolved, ["03"])

    def test_next_refuses_to_skip_terminal_human_gate_or_external_block(self) -> None:
        self.run_state.get_item_state("01").terminal_state = "awaiting_human_gate"
        with self.assertRaisesRegex(OrchestratorError, "awaiting_human_gate"):
            self.orchestrator._resolve_requested_items(
                plan=self.plan,
                run_state=self.run_state,
                explicit_item=None,
                explicit_items=[],
                next_only=True,
            )

        self.run_state.get_item_state("01").terminal_state = "blocked_external"
        with self.assertRaisesRegex(OrchestratorError, "blocked_external"):
            self.orchestrator._resolve_requested_items(
                plan=self.plan,
                run_state=self.run_state,
                explicit_item=None,
                explicit_items=[],
                next_only=True,
            )

    def test_repo_input_guard_rejects_untracked_consult_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            init_git_repo(repo_root)
            tracked_path = repo_root / "docs" / "runbooks" / "policy_note.md"
            tracked_path.parent.mkdir(parents=True, exist_ok=True)
            tracked_path.write_text("tracked\n", encoding="utf-8")
            git_commit_all(repo_root, "seed tracked docs")

            untracked_path = repo_root / "docs" / "runbooks" / "untracked.md"
            untracked_path.write_text("untracked\n", encoding="utf-8")

            orchestrator = PlanOrchestrator(repo_root)
            item = make_item("01", 1)
            item.consult_paths = ["docs/runbooks/untracked.md"]

            with self.assertRaisesRegex(OrchestratorError, "untracked"):
                orchestrator._assert_item_repo_inputs_available(item=item)

    def test_resume_restarts_blocked_external_item_from_fresh_attempt_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            orchestrator = PlanOrchestrator(repo_root)
            plan = make_plan()
            run_id = "RUN_TEST_RESUME_BLOCKED"
            dirs = resolve_run_directories(repo_root, run_id)
            normalized_plan_path = repo_root / "normalized_plan.json"
            normalized_plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path="playbook.md",
                playbook_source_sha256="a" * 64,
                normalized_plan_path=normalized_plan_path.relative_to(repo_root).as_posix(),
                base_head_sha="deadbeef",
                run_branch_name="orchestrator/run/RUN_TEST_RESUME_BLOCKED",
                options=make_options(),
                plan=plan,
            )
            run_state.current_state = StateId.ST120_BLOCKED_EXTERNAL.value
            run_state.current_item_id = "01"
            item_state = run_state.get_item_state("01")
            item_state.state = StateId.ST120_BLOCKED_EXTERNAL.value
            item_state.terminal_state = "blocked_external"
            save_run_state(dirs.run_state_path, run_state)

            evidence_dir = repo_root / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)

            def fake_run_item_attempt(**kwargs):
                self.assertEqual(kwargs["item_id"], "01")
                self.assertEqual(kwargs["run_state"].current_state, StateId.ST05_PLAN_NORMALIZED.value)
                self.assertIsNone(kwargs["run_state"].current_item_id)
                self.assertEqual(kwargs["run_state"].get_item_state("01").state, StateId.ST05_PLAN_NORMALIZED.value)
                return "passed"

            with mock.patch.object(
                orchestrator,
                "_preflight_for_run",
                return_value=SimpleNamespace(),
            ), mock.patch.object(
                orchestrator,
                "_run_item_attempt",
                side_effect=fake_run_item_attempt,
            ):
                result = orchestrator.resume(
                    run_id=run_id,
                    external_evidence_dir=evidence_dir.as_posix(),
                    auto_advance=False,
                )

            self.assertEqual(result["last_terminal_state"], "passed")

    def test_run_new_persists_terminal_state_when_item_attempt_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            orchestrator = PlanOrchestrator(repo_root)
            plan = make_plan()
            parsed = {
                "sha256": "b" * 64,
                "raw_markdown": "# playbook\n",
                "ordered_execution_rows": [],
                "sections": [],
            }

            def raising_attempt(*, run_state, **_kwargs):
                item_state = run_state.get_item_state("01")
                orchestrator._set_state(
                    run_state,
                    item_state,
                    StateId.ST140_ESCALATED,
                    "orchestrator",
                    "boom",
                )
                item_state.terminal_state = "escalated"
                orchestrator._persist_active_run_state(run_state)
                raise OrchestratorError("boom")

            fake_manager = SimpleNamespace(
                current_head_sha=lambda: "deadbeef",
                ensure_run_branch=lambda run_id, _head: f"orchestrator/run/{run_id}",
            )

            with mock.patch(
                "automation.plan_orchestrator.runtime.make_run_id",
                return_value="RUN_TEST_PERSIST",
            ), mock.patch(
                "automation.plan_orchestrator.runtime.parse_playbook",
                return_value=parsed,
            ), mock.patch.object(
                orchestrator.adapter,
                "normalize",
                return_value=plan,
            ), mock.patch.object(
                orchestrator,
                "_preflight_for_run",
                return_value=fake_manager,
            ), mock.patch.object(
                orchestrator,
                "_run_item_attempt",
                side_effect=raising_attempt,
            ):
                with self.assertRaises(OrchestratorError):
                    orchestrator.run_new(playbook_path="playbook.md", item_id="01")

            saved = load_run_state(resolve_run_directories(repo_root, "RUN_TEST_PERSIST").run_state_path)
            self.assertEqual(saved.current_state, StateId.ST140_ESCALATED.value)
            self.assertEqual(saved.current_item_id, "01")
            self.assertEqual(saved.get_item_state("01").terminal_state, "escalated")

    def test_manual_gate_approval_fast_forwards_run_branch_to_reviewed_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            tracked = repo_root / "docs" / "runbooks" / "policy_note.md"
            tracked.parent.mkdir(parents=True, exist_ok=True)
            tracked.write_text("base\n", encoding="utf-8")
            base_ref = git_commit_all(repo_root, "base")

            subprocess.run(["git", "branch", "orchestrator/run/RUN_TEST_APPROVE"], cwd=repo_root, check=True)

            tracked.write_text("checkpoint\n", encoding="utf-8")
            checkpoint_ref = git_commit_all(repo_root, "checkpoint")

            orchestrator = PlanOrchestrator(repo_root)
            plan = make_plan()
            plan.get_item("01").manual_gate = {
                "required": True,
                "gate_type": "signoff",
                "gate_reason": "Need signoff.",
                "required_evidence": ["signed note"],
            }
            run_id = "RUN_TEST_APPROVE"
            dirs = resolve_run_directories(repo_root, run_id)
            run_state = create_run_state(
                run_id=run_id,
                adapter_id="markdown_playbook_v1",
                repo_root=repo_root.as_posix(),
                playbook_source_path="playbook.md",
                playbook_source_sha256="f" * 64,
                normalized_plan_path="normalized_plan.json",
                base_head_sha=base_ref,
                run_branch_name="orchestrator/run/RUN_TEST_APPROVE",
                options=make_options(),
                plan=plan,
            )
            run_state.current_state = StateId.ST110_AWAITING_HUMAN_GATE.value
            run_state.current_item_id = "01"
            item_state = run_state.get_item_state("01")
            item_state.state = StateId.ST110_AWAITING_HUMAN_GATE.value
            item_state.terminal_state = "awaiting_human_gate"
            item_state.manual_gate_status = "pending"
            item_state.branch_name = "orchestrator/item/RUN_TEST_APPROVE/01/attempt-1"
            item_state.worktree_path = "worktree-01"
            item_state.checkpoint_ref = checkpoint_ref
            item_state.latest_paths.artifact_manifest_path = "items/01/attempt-1/artifact_manifest.json"

            manual_gate_path = dirs.item_control_dir("01", 1) / "manual_gate.json"
            write_manual_gate_record(
                output_path=manual_gate_path,
                run_id=run_id,
                item_id="01",
                gate_id="gate_01",
                gate_type="signoff",
                status="pending",
                requested_by="orchestrator",
                requested_reason="Need signoff.",
                required_evidence=["signed note"],
                branch_name=item_state.branch_name,
                worktree_path=item_state.worktree_path,
                checkpoint_ref=checkpoint_ref,
                artifact_manifest_path=item_state.latest_paths.artifact_manifest_path,
                triage_report_path=None,
                merged_findings_packet_path=None,
                codex_audit_report_path=None,
                claude_audit_report_path=None,
                review_findings=[],
                decision=None,
            )
            item_state.latest_paths.manual_gate_path = manual_gate_path.relative_to(repo_root).as_posix()
            save_run_state(dirs.run_state_path, run_state)

            result = orchestrator.mark_manual_gate(
                run_id=run_id,
                item_id="01",
                decision="approved",
                decided_by="reviewer",
                note="Approved.",
                evidence_paths=[],
            )

            resolved_run_head = subprocess.run(
                ["git", "rev-parse", "orchestrator/run/RUN_TEST_APPROVE"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            self.assertEqual(result["decision"], "approved")
            self.assertEqual(resolved_run_head, checkpoint_ref)
