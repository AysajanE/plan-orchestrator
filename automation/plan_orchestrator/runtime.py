from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .adapters import PlanAdapter, build_default_adapter
from .config import (
    CLAUDE_MAX_TURNS_DEFAULT,
    RunDirectories,
    assert_clean_agent_environment,
    make_run_id,
    prompt_file,
    resolve_run_directories,
    resolve_runtime_policy,
    schema_file,
    runtime_policy_snapshot_payload,
)
from .findings import MUTATION_REPORT_SOURCE_TYPE, write_merged_findings_packet
from .git_checkpoint import (
    ScopeViolation,
    collect_post_checkpoint_authority_violations,
    create_checkpoint_commit,
    generate_patch,
    stage_allowed_changes,
)
from .models import ItemContext, NormalizedPlan, PlanItem, RunState
from .playbook_parser import parse_playbook
from .reporting import (
    artifact_spec,
    build_artifact_manifest,
    render_template,
    workspace_path_for_artifact,
    workspace_path_for_manifest_entry,
    write_escalation_manifest,
    write_manual_gate_record,
    write_pass_summary,
    write_playbook_snapshot,
)
from .state_machine import (
    StateId,
    assert_transition,
    first_unfinished_item,
    prerequisites_satisfied,
)
from .state_store import (
    append_event,
    create_run_state,
    load_run_state,
    save_run_state,
    touch_item_state,
)
from .subprocess_runner import StageProcessError, run_claude_audit, run_codex_stage
from .validators import (
    compute_sha256,
    dedupe_preserve_order,
    ensure_directory,
    load_json,
    repo_relative_path,
    resolve_repo_path,
    utc_now_iso,
    validate_named_schema,
    write_json_atomic,
)
from .verification import run_verification
from .worktree_manager import GitError, WorktreeManager


class OrchestratorError(RuntimeError):
    pass


class PlanOrchestrator:
    def __init__(self, repo_root: Path, adapter: PlanAdapter | None = None) -> None:
        self.repo_root = repo_root
        self.adapter = adapter or build_default_adapter(repo_root)
        self._active_run_state_path: Path | None = None

    # -------------------------------
    # public query helpers
    # -------------------------------

    def list_items(self, playbook_path: str | Path) -> list[dict[str, Any]]:
        plan = self._normalized_plan_from_playbook(Path(playbook_path))
        return [item.to_dict() for item in sorted(plan.items, key=lambda value: value.order)]

    def show_item(self, playbook_path: str | Path, item_id: str) -> dict[str, Any]:
        plan = self._normalized_plan_from_playbook(Path(playbook_path))
        return plan.get_item(item_id).to_dict()

    # -------------------------------
    # run entrypoints
    # -------------------------------

    def run_new(
        self,
        *,
        playbook_path: str | Path,
        item_id: str | None = None,
        item_ids: list[str] | None = None,
        next_only: bool = False,
        external_evidence_dir: str | None = None,
        auto_advance: bool | None = None,
        max_items: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        playbook = resolve_repo_path(self.repo_root, playbook_path)

        run_id = make_run_id()
        dirs = resolve_run_directories(self.repo_root, run_id)
        policy = resolve_runtime_policy(
            self.repo_root,
            config_path=config_path,
            cli_auto_advance=auto_advance,
            cli_max_items=max_items,
        )
        options = policy.options
        manager = self._preflight_for_run(worktrees_root=dirs.worktrees_root)

        parsed = parse_playbook(playbook)
        snapshot_path = dirs.run_root / "playbook_source_snapshot.md"
        write_playbook_snapshot(
            source_path=playbook,
            source_sha256=parsed["sha256"],
            source_text=parsed["raw_markdown"],
            output_path=snapshot_path,
        )

        plan = self.adapter.normalize(parsed, playbook)
        normalized_plan_path = dirs.run_root / "normalized_plan.json"
        write_json_atomic(normalized_plan_path, plan.to_dict())
        runtime_policy_path = dirs.run_root / "runtime_policy.json"
        write_json_atomic(runtime_policy_path, runtime_policy_snapshot_payload(policy))
        runtime_policy_sha256 = compute_sha256(runtime_policy_path)

        run_branch_name = manager.ensure_run_branch(run_id, manager.current_head_sha())
        run_state = create_run_state(
            run_id=run_id,
            adapter_id=self.adapter.adapter_id,
            repo_root=self.repo_root.as_posix(),
            playbook_source_path=repo_relative_path(self.repo_root, playbook),
            playbook_source_sha256=parsed["sha256"],
            normalized_plan_path=repo_relative_path(self.repo_root, normalized_plan_path),
            base_head_sha=manager.current_head_sha(),
            run_branch_name=run_branch_name,
            options=options,
            plan=plan,
            runtime_policy_path=repo_relative_path(self.repo_root, runtime_policy_path),
            runtime_policy_sha256=runtime_policy_sha256,
            runtime_policy_sources=policy.sources,
        )
        run_state.current_state = StateId.ST05_PLAN_NORMALIZED.value
        append_event(run_state, actor="orchestrator", message="Playbook normalized.")
        save_run_state(dirs.run_state_path, run_state)
        self._active_run_state_path = dirs.run_state_path

        try:
            requested = self._resolve_requested_items(
                plan=plan,
                run_state=run_state,
                explicit_item=item_id,
                explicit_items=item_ids or [],
                next_only=next_only,
            )

            last_terminal = self._run_requested_items(
                plan=plan,
                run_state=run_state,
                dirs=dirs,
                item_ids=requested,
                external_evidence_dir=external_evidence_dir,
                continue_after_queue=options.auto_advance and item_ids is None,
                max_items=options.max_items,
            )

            return {
                "run_id": run_state.run_id,
                "current_state": run_state.current_state,
                "current_item_id": run_state.current_item_id,
                "last_terminal_state": last_terminal,
                "run_state_path": repo_relative_path(self.repo_root, dirs.run_state_path),
            }
        finally:
            self._active_run_state_path = None

    def resume(
        self,
        *,
        run_id: str,
        external_evidence_dir: str | None = None,
        auto_advance: bool = False,
    ) -> dict[str, Any]:
        dirs = resolve_run_directories(self.repo_root, run_id)
        self._preflight_for_run(worktrees_root=dirs.worktrees_root)
        run_state = load_run_state(dirs.run_state_path)
        plan = NormalizedPlan.from_dict(
            load_json(resolve_repo_path(self.repo_root, run_state.normalized_plan_path))
        )

        self._active_run_state_path = dirs.run_state_path
        try:
            if run_state.current_state == StateId.ST110_AWAITING_HUMAN_GATE.value:
                raise OrchestratorError(
                    "Current item is awaiting a human gate. Use mark-manual-gate before resume."
                )

            if run_state.current_item_id and run_state.current_state == StateId.ST120_BLOCKED_EXTERNAL.value:
                target_item_id = run_state.current_item_id
            elif run_state.current_item_id and run_state.current_state == StateId.ST140_ESCALATED.value:
                target_item_id = run_state.current_item_id
            else:
                unfinished = first_unfinished_item(plan.items, run_state.items)
                if unfinished is None:
                    return {
                        "run_id": run_state.run_id,
                        "current_state": run_state.current_state,
                        "current_item_id": None,
                        "last_terminal_state": "passed",
                        "run_state_path": repo_relative_path(self.repo_root, dirs.run_state_path),
                    }
                target_item_id = unfinished.item_id

            target_item_state = run_state.get_item_state(target_item_id)
            if target_item_state.terminal_state == "blocked_external":
                if not external_evidence_dir:
                    raise OrchestratorError(
                        "Resume of a blocked_external item requires --external-evidence-dir."
                    )
                self._reset_item_for_resume(
                    run_state=run_state,
                    item_state=target_item_state,
                    reason=(
                        f"Reset item {target_item_id} from blocked_external for a fresh resume attempt."
                    ),
                )
            elif target_item_state.terminal_state == "escalated":
                self._reset_item_for_resume(
                    run_state=run_state,
                    item_state=target_item_state,
                    reason=f"Reset item {target_item_id} from escalated for a fresh resume attempt.",
                )

            last_terminal = self._run_requested_items(
                plan=plan,
                run_state=run_state,
                dirs=dirs,
                item_ids=[target_item_id],
                external_evidence_dir=external_evidence_dir,
                continue_after_queue=auto_advance,
                max_items=run_state.options.max_items if auto_advance else None,
            )

            return {
                "run_id": run_state.run_id,
                "current_state": run_state.current_state,
                "current_item_id": run_state.current_item_id,
                "last_terminal_state": last_terminal,
                "run_state_path": repo_relative_path(self.repo_root, dirs.run_state_path),
            }
        finally:
            self._active_run_state_path = None

    def refresh_run(
        self,
        *,
        run_id: str,
        retarget_run_branch_to: str,
    ) -> dict[str, Any]:
        dirs = resolve_run_directories(self.repo_root, run_id)
        manager = self._preflight_for_run(worktrees_root=dirs.worktrees_root)
        run_state = load_run_state(dirs.run_state_path)

        snapshot_path = dirs.run_root / "playbook_source_snapshot.md"
        plan = self._normalized_plan_from_playbook_snapshot(
            snapshot_path=snapshot_path,
            preserved_playbook_path=Path(run_state.playbook_source_path),
        )
        normalized_plan_path = resolve_repo_path(self.repo_root, run_state.normalized_plan_path)
        write_json_atomic(normalized_plan_path, plan.to_dict())

        new_run_branch_name = manager.create_run_refresh_branch(
            run_id=run_state.run_id,
            current_run_branch_name=run_state.run_branch_name,
            target_ref=retarget_run_branch_to,
        )
        target_sha = manager.resolve_ref(retarget_run_branch_to)

        run_state.run_branch_name = new_run_branch_name
        append_event(
            run_state,
            actor="orchestrator",
            message=(
                f"Run refreshed onto {new_run_branch_name} at {target_sha}; "
                "normalized plan rebuilt from the saved playbook snapshot."
            ),
        )
        save_run_state(dirs.run_state_path, run_state)

        return {
            "run_id": run_state.run_id,
            "current_state": run_state.current_state,
            "current_item_id": run_state.current_item_id,
            "run_branch_name": run_state.run_branch_name,
            "run_state_path": repo_relative_path(self.repo_root, dirs.run_state_path),
            "normalized_plan_path": repo_relative_path(self.repo_root, normalized_plan_path),
        }

    def mark_manual_gate(
        self,
        *,
        run_id: str,
        item_id: str,
        decision: str,
        decided_by: str,
        note: str,
        evidence_paths: list[str],
    ) -> dict[str, Any]:
        dirs = resolve_run_directories(self.repo_root, run_id)
        run_state = load_run_state(dirs.run_state_path)
        item_state = run_state.get_item_state(item_id)
        if item_state.latest_paths.manual_gate_path is None:
            raise OrchestratorError("No manual gate record exists for this item.")

        manual_gate_path = resolve_repo_path(self.repo_root, item_state.latest_paths.manual_gate_path)
        gate = load_json(manual_gate_path)
        if gate["status"] != "pending":
            raise OrchestratorError("Manual gate is not pending.")

        decision_payload = {
            "outcome": decision,
            "decided_at_utc": utc_now_iso(),
            "decided_by": decided_by,
            "note": note,
            "evidence_paths": evidence_paths,
        }
        updated = write_manual_gate_record(
            output_path=manual_gate_path,
            run_id=run_state.run_id,
            item_id=item_state.item_id,
            gate_id=gate["gate_id"],
            gate_type=gate["gate_type"],
            status="approved" if decision == "approved" else "rejected",
            requested_by=gate["requested_by"],
            requested_reason=gate["requested_reason"],
            required_evidence=gate["required_evidence"],
            branch_name=gate["related_refs"]["branch_name"],
            worktree_path=gate["related_refs"]["worktree_path"],
            checkpoint_ref=gate["related_refs"]["checkpoint_ref"],
            artifact_manifest_path=gate["related_refs"]["artifact_manifest_path"],
            triage_report_path=gate["related_refs"].get("triage_report_path"),
            merged_findings_packet_path=gate["related_refs"].get("merged_findings_packet_path"),
            codex_audit_report_path=gate["related_refs"].get("codex_audit_report_path"),
            claude_audit_report_path=gate["related_refs"].get("claude_audit_report_path"),
            review_findings=list(gate.get("review_findings", [])),
            decision=decision_payload,
        )

        item_state.manual_gate_status = "approved" if decision == "approved" else "rejected"
        if decision == "approved":
            manager = WorktreeManager(self.repo_root, dirs.worktrees_root)
            checkpoint_ref = gate["related_refs"]["checkpoint_ref"]
            if not checkpoint_ref:
                raise OrchestratorError("Manual gate approval requires a reviewed checkpoint_ref.")
            manager.fast_forward_run_branch_to_ref(run_state.run_branch_name, checkpoint_ref)
            self._set_state(run_state, item_state, StateId.ST130_PASSED, "human", "Manual gate approved.")
            item_state.terminal_state = "passed"
        else:
            self._terminal_escalated(
                run_state=run_state,
                item_state=item_state,
                control_dir=manual_gate_path.parent,
                summary=f"Manual gate rejected by {decided_by}: {note}",
                artifact_manifest_path=gate["related_refs"]["artifact_manifest_path"],
            )

        item_state.latest_paths.manual_gate_path = repo_relative_path(self.repo_root, manual_gate_path)
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)
        save_run_state(dirs.run_state_path, run_state)

        return {
            "run_id": run_state.run_id,
            "item_id": item_state.item_id,
            "decision": updated["status"],
            "current_state": run_state.current_state,
            "run_state_path": repo_relative_path(self.repo_root, dirs.run_state_path),
        }

    # -------------------------------
    # internals
    # -------------------------------

    def _normalized_plan_from_playbook(self, playbook_path: Path) -> NormalizedPlan:
        parsed = parse_playbook(playbook_path)
        return self.adapter.normalize(parsed, playbook_path)

    def _absolute_packet_path(self, *, worktree_path: Path, workspace_packet_path: str) -> str:
        packet_path = Path(workspace_packet_path)
        if packet_path.is_absolute():
            return packet_path.as_posix()
        return (worktree_path / packet_path).as_posix()

    def _round_suffix_for_stage(
        self,
        *,
        stage_name: str,
        fix_round_index: int,
        remediation_round_index: int,
    ) -> str:
        if stage_name == "execute":
            return "round-0"
        if stage_name == "fix":
            return f"round-{fix_round_index}"
        if stage_name == "remediation":
            return f"round-{remediation_round_index}"
        raise OrchestratorError(f"Unknown stage for round suffix: {stage_name}")

    def _stage_cycle_identity(
        self,
        *,
        item_state: Any,
        stage_name: str,
    ) -> tuple[str, str, str]:
        round_suffix = self._round_suffix_for_stage(
            stage_name=stage_name,
            fix_round_index=int(item_state.fix_rounds_completed),
            remediation_round_index=int(item_state.remediation_rounds_completed),
        )
        return stage_name, round_suffix, f"{stage_name}.{round_suffix}"

    def _current_cycle_identity(self, *, item_state: Any) -> tuple[str, str, str]:
        item_context_path = item_state.latest_paths.item_context_path
        if item_context_path:
            resolved = resolve_repo_path(self.repo_root, item_context_path)
            if resolved.exists():
                context = load_json(resolved)
                stage_context = context.get("stage_context", {})
                stage_name = str(stage_context.get("stage") or "execute")
                round_suffix = self._round_suffix_for_stage(
                    stage_name=stage_name,
                    fix_round_index=int(stage_context.get("fix_round_index") or 0),
                    remediation_round_index=int(stage_context.get("remediation_round_index") or 0),
                )
                return stage_name, round_suffix, f"{stage_name}.{round_suffix}"

        for candidate in filter(
            None,
            [
                item_state.latest_paths.fix_report_path,
                item_state.latest_paths.execution_report_path,
            ],
        ):
            name = Path(candidate).name
            stage_name = "execute"
            if name.startswith("remediation_report"):
                stage_name = "remediation"
            elif name.startswith("fix_report"):
                stage_name = "fix"
            match = re.search(r"round-(\d+)", name)
            round_suffix = f"round-{match.group(1)}" if match else "round-0"
            return stage_name, round_suffix, f"{stage_name}.{round_suffix}"

        return "execute", "round-0", "execute.round-0"

    def _latest_control_artifact(self, *, control_dir: Path, stem: str) -> Path | None:
        candidates = [path for path in control_dir.glob(f"{stem}*.json") if path.is_file()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))

    def _manual_gate_review_findings(self, *, triage_report_path: str | Path | None) -> list[dict[str, Any]]:
        if triage_report_path is None:
            return []
        report = load_json(resolve_repo_path(self.repo_root, triage_report_path))
        findings = []
        for finding in report.get("merged_findings", []):
            if (
                finding.get("recommended_owner") == "human"
                or finding.get("category") == "manual_gate"
                or finding.get("disposition") == "requires_human_judgment"
            ):
                findings.append(dict(finding))
        return findings

    def _desired_decision_for_mutation_control(
        self,
        *,
        signals: list[str],
        current_decision: str,
        item: Any,
    ) -> str | None:
        signal_set = set(signals)
        if "blocked_external" in signal_set:
            return "blocked_external"
        if "escalate" in signal_set:
            return "escalate"
        if "needs_human_input" in signal_set:
            return "awaiting_human_gate" if bool(item.manual_gate.get("required")) else "escalate"
        if "unresolved_items" in signal_set and current_decision == "pass":
            return "fix_required"
        return None

    def _is_repo_content_path(self, path_value: Any) -> bool:
        candidate = str(path_value or "").strip()
        if not candidate:
            return False
        if candidate.startswith(".local/"):
            return False
        if candidate.startswith("/"):
            try:
                relative = Path(candidate).resolve().relative_to(self.repo_root.resolve())
            except ValueError:
                return False
            return not str(relative).startswith(".local/")
        return True

    def _blocking_repo_content_findings(self, *, triage_report: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for finding in triage_report.get("merged_findings", []):
            if not finding.get("is_blocking"):
                continue
            if not any(
                source_ref.get("source_type") in {"codex_audit", "claude_audit", "verification"}
                for source_ref in finding.get("source_refs", [])
            ):
                continue
            if any(self._is_repo_content_path(path) for path in finding.get("file_paths", [])):
                findings.append(dict(finding))
        return findings

    def _preserve_mutation_control_handoff(
        self,
        *,
        item: Any,
        triage_report: dict[str, Any],
        triage_report_path: Path,
        merged_packet: dict[str, Any],
    ) -> dict[str, Any]:
        control = merged_packet.get("mutation_report_control", {})
        signal_refs = control.get("signal_refs", [])
        if not signal_refs:
            return triage_report

        suppressed_source_ids = {
            entry["source_id"]
            for entry in triage_report.get("suppressed_findings", [])
            if entry.get("source_type") == MUTATION_REPORT_SOURCE_TYPE
        }
        packet_findings = [
            dict(finding)
            for finding in merged_packet.get("merged_findings", [])
            if any(
                source_ref.get("source_type") == MUTATION_REPORT_SOURCE_TYPE
                for source_ref in finding.get("source_refs", [])
            )
        ]
        merged_by_id = {
            finding["canonical_id"]: dict(finding) for finding in triage_report.get("merged_findings", [])
        }
        unsuppressed_signals: list[str] = []
        injected_titles: list[str] = []

        for finding in packet_findings:
            mutation_source_ids = [
                source_ref["source_id"]
                for source_ref in finding.get("source_refs", [])
                if source_ref.get("source_type") == MUTATION_REPORT_SOURCE_TYPE
            ]
            active_source_ids = [source_id for source_id in mutation_source_ids if source_id not in suppressed_source_ids]
            if not active_source_ids:
                continue

            if finding["canonical_id"] not in merged_by_id:
                merged_by_id[finding["canonical_id"]] = finding
                injected_titles.append(finding["title"])

            for signal_ref in signal_refs:
                if signal_ref["source_id"] in active_source_ids:
                    unsuppressed_signals.append(signal_ref["signal"])

        if injected_titles:
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
            triage_report["merged_findings"] = sorted(
                merged_by_id.values(),
                key=lambda value: (severity_order[value["severity"]], value["canonical_id"]),
            )

        reasoning_notes = list(triage_report.get("reasoning_notes", []))
        if injected_titles:
            reasoning_notes.append(
                "Orchestrator preserved mutation-stage control findings: "
                + "; ".join(injected_titles)
                + "."
            )

        desired_decision = self._desired_decision_for_mutation_control(
            signals=dedupe_preserve_order(unsuppressed_signals),
            current_decision=triage_report["overall_decision"],
            item=item,
        )
        if desired_decision and triage_report["overall_decision"] != desired_decision:
            triage_report["overall_decision"] = desired_decision
            triage_report["next_stage"] = {
                "pass": "passed",
                "fix_required": "fix",
                "awaiting_human_gate": "awaiting_human_gate",
                "blocked_external": "blocked_external",
                "escalate": "escalated",
            }[desired_decision]
            reasoning_notes.append(
                f"Orchestrator preserved mutation-stage control state as `{desired_decision}`."
            )
            triage_report["summary"] = (
                f"{triage_report['summary']} | mutation-stage control state preserved as {desired_decision}"
            )

        blocking_repo_findings = self._blocking_repo_content_findings(triage_report=triage_report)
        if triage_report["overall_decision"] in {"pass", "awaiting_human_gate"} and blocking_repo_findings:
            triage_report["overall_decision"] = "fix_required"
            triage_report["next_stage"] = "fix"
            blocking_titles = "; ".join(finding["title"] for finding in blocking_repo_findings)
            reasoning_notes.append(
                "Orchestrator preserved blocking audit/verification repo-content findings as `fix_required`: "
                + blocking_titles
                + "."
            )
            triage_report["summary"] = (
                f"{triage_report['summary']} | blocking audit/verification repo-content findings preserved as fix_required"
            )

        if reasoning_notes != triage_report.get("reasoning_notes", []):
            triage_report["reasoning_notes"] = dedupe_preserve_order(reasoning_notes)

        if injected_titles or desired_decision or blocking_repo_findings:
            validate_named_schema("triage_report.schema.json", triage_report)
            write_json_atomic(triage_report_path, triage_report)

        return triage_report

    def _preflight_for_run(self, *, worktrees_root: Path) -> WorktreeManager:
        assert_clean_agent_environment(self.repo_root)
        manager = WorktreeManager(self.repo_root, worktrees_root)
        manager.ensure_commands_available()
        manager.assert_git_identity_available()
        manager.assert_clean_tracked_checkout()
        return manager

    def _normalized_plan_from_playbook_snapshot(
        self,
        *,
        snapshot_path: Path,
        preserved_playbook_path: Path,
    ) -> NormalizedPlan:
        if not snapshot_path.exists():
            raise OrchestratorError(f"Missing playbook snapshot for refresh: {snapshot_path}")

        snapshot_text = snapshot_path.read_text(encoding="utf-8")
        try:
            playbook_source = snapshot_text.split("\n---\n\n", 1)[1]
        except IndexError as exc:
            raise OrchestratorError(
                f"Playbook snapshot is malformed and cannot be refreshed: {snapshot_path}"
            ) from exc

        with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as handle:
            handle.write(playbook_source)
            temp_path = Path(handle.name)

        try:
            parsed = parse_playbook(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

        return self.adapter.normalize(parsed, preserved_playbook_path)

    def _persist_active_run_state(self, run_state: RunState) -> None:
        if self._active_run_state_path is not None:
            save_run_state(self._active_run_state_path, run_state)

    def _run_requested_items(
        self,
        *,
        plan: NormalizedPlan,
        run_state: RunState,
        dirs: RunDirectories,
        item_ids: list[str],
        external_evidence_dir: str | None,
        continue_after_queue: bool,
        max_items: int | None,
    ) -> str:
        pending = list(item_ids)
        processed = 0
        last_terminal = "none"

        while pending:
            if max_items is not None and processed >= max_items:
                break
            current_item_id = pending.pop(0)
            current_external_evidence_dir = external_evidence_dir if processed == 0 else None
            self._assert_item_repo_inputs_available(item=plan.get_item(current_item_id))
            try:
                last_terminal = self._run_item_attempt(
                    plan=plan,
                    run_state=run_state,
                    dirs=dirs,
                    item_id=current_item_id,
                    external_evidence_dir=current_external_evidence_dir,
                )
            except Exception:
                self._persist_active_run_state(run_state)
                raise
            self._persist_active_run_state(run_state)
            processed += 1
            if last_terminal != "passed":
                break
            if continue_after_queue and not pending:
                next_item_id = self._next_auto_advance_item(plan=plan, run_state=run_state)
                if next_item_id is not None:
                    pending.append(next_item_id)

        return last_terminal

    def _git_path_query(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo_root),
            text=True,
            capture_output=True,
            check=False,
        )

    def _repo_input_status(self, rel_path: str) -> tuple[bool, bool, bool]:
        if Path(rel_path).is_absolute():
            return True, False, Path(rel_path).exists()

        tracked = self._git_path_query("ls-files", "--error-unmatch", "--", rel_path).returncode == 0
        ignored = self._git_path_query("check-ignore", "--quiet", "--", rel_path).returncode == 0
        exists = (self.repo_root / rel_path).exists()
        return tracked, ignored, exists

    def _assert_item_repo_inputs_available(self, *, item: PlanItem) -> None:
        if not (self.repo_root / ".git").exists():
            return

        unresolved: list[tuple[str, str]] = []
        for rel_path in dedupe_preserve_order(item.consult_paths):
            tracked, ignored, exists = self._repo_input_status(rel_path)
            if tracked:
                continue

            if ignored:
                status = "ignored"
            elif exists:
                status = "untracked"
            else:
                status = "missing"
            unresolved.append((rel_path, status))

        if not unresolved:
            return

        details = "\n".join(f"- {path} ({status})" for path, status in unresolved)
        raise OrchestratorError(
            f"Item {item.item_id} references repo inputs that cannot be materialized into an orchestrator worktree.\n"
            "Commit or promote the required inputs into tracked repo paths before running the item.\n"
            f"{details}"
        )

    def _reset_item_for_resume(
        self,
        *,
        run_state: RunState,
        item_state: Any,
        reason: str,
    ) -> None:
        run_state.current_state = StateId.ST05_PLAN_NORMALIZED.value
        run_state.current_item_id = None
        item_state.state = StateId.ST05_PLAN_NORMALIZED.value
        touch_item_state(item_state)
        append_event(run_state, actor="orchestrator", message=reason)
        self._persist_active_run_state(run_state)

    def _resolve_requested_items(
        self,
        *,
        plan: NormalizedPlan,
        run_state: RunState,
        explicit_item: str | None,
        explicit_items: list[str],
        next_only: bool,
    ) -> list[str]:
        specified = bool(explicit_item) + bool(explicit_items) + bool(next_only)
        if specified != 1:
            raise OrchestratorError("Choose exactly one of --item, --items, or --next.")

        if explicit_item:
            return [explicit_item]

        if explicit_items:
            return explicit_items

        next_item_id = self._next_auto_advance_item(plan=plan, run_state=run_state)
        if next_item_id is None:
            return []
        return [next_item_id]

    def _next_auto_advance_item(
        self,
        *,
        plan: NormalizedPlan,
        run_state: RunState,
    ) -> str | None:
        unfinished = first_unfinished_item(plan.items, run_state.items)
        if unfinished is None:
            return None
        prior_state = run_state.get_item_state(unfinished.item_id)
        if prior_state.terminal_state == "awaiting_human_gate":
            raise OrchestratorError(
                f"First unfinished item {unfinished.item_id} is awaiting_human_gate. Use mark-manual-gate."
            )
        if prior_state.terminal_state in {"blocked_external", "escalated"}:
            raise OrchestratorError(
                f"First unfinished item {unfinished.item_id} is {prior_state.terminal_state}. Resolve it before advancing."
            )
        if not prerequisites_satisfied(unfinished, run_state.items):
            raise OrchestratorError(
                f"First unfinished item {unfinished.item_id} has unmet prerequisites."
            )
        return unfinished.item_id

    def _next_attempt_number(self, item_state: Any) -> int:
        if item_state.branch_name or item_state.worktree_path or item_state.terminal_state != "none":
            return item_state.attempt_number + 1
        return item_state.attempt_number

    def _set_state(
        self,
        run_state: RunState,
        item_state: Any,
        new_state: StateId,
        actor: str,
        message: str,
    ) -> None:
        assert_transition(run_state.current_state, new_state.value)
        run_state.current_state = new_state.value
        run_state.current_item_id = item_state.item_id
        item_state.state = new_state.value
        touch_item_state(item_state)
        append_event(run_state, actor=actor, message=message)
        self._persist_active_run_state(run_state)

    def _mark_mutation_stage_running(
        self,
        *,
        run_state: RunState,
        item_state: Any,
        stage_name: str,
    ) -> None:
        if stage_name == "execute":
            self._set_state(
                run_state,
                item_state,
                StateId.ST30_EXECUTING,
                "orchestrator",
                f"Running {stage_name} stage.",
            )
            return

        item_state.state = run_state.current_state
        touch_item_state(item_state)
        append_event(run_state, actor="orchestrator", message=f"Running {stage_name} stage.")
        self._persist_active_run_state(run_state)

    def _copy_external_evidence(
        self,
        *,
        source_dir: str | None,
        destination_dir: Path,
    ) -> Path | None:
        if not source_dir:
            return None
        src = Path(source_dir)
        if not src.exists() or not src.is_dir():
            raise OrchestratorError(f"External evidence directory does not exist: {source_dir}")
        ensure_directory(destination_dir.parent)
        if destination_dir.exists():
            import shutil
            shutil.rmtree(destination_dir)
        import shutil
        shutil.copytree(src, destination_dir)
        return destination_dir

    def _content_type_for_path(self, path: Path) -> str:
        if path.is_dir():
            return "directory"
        return {
            ".json": "json",
            ".md": "markdown",
            ".txt": "text",
            ".log": "log",
            ".patch": "diff",
            ".diff": "diff",
        }.get(path.suffix.lower(), "other")

    def _tracked_repo_input_specs(self, *, item: PlanItem, consumer_stage: str) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for index, rel_path in enumerate(dedupe_preserve_order(item.consult_paths), start=1):
            resolved = resolve_repo_path(self.repo_root, rel_path)
            specs.append(
                artifact_spec(
                    logical_name=f"consult_input_{index:02d}",
                    path=resolved,
                    content_type=self._content_type_for_path(resolved),
                    storage_class="tracked_repo",
                    git_policy="tracked",
                    trust_level="source_input",
                    producer="repo",
                    consumers=[consumer_stage],
                    must_exist=True,
                    description=f"Tracked repo input for item context: {rel_path}",
                )
            )
        return specs

    def _run_item_attempt(
        self,
        *,
        plan: NormalizedPlan,
        run_state: RunState,
        dirs: RunDirectories,
        item_id: str,
        external_evidence_dir: str | None,
    ) -> str:
        item = plan.get_item(item_id)
        item_state = run_state.get_item_state(item_id)

        if not prerequisites_satisfied(item, run_state.items):
            raise OrchestratorError(f"Item {item_id} has unmet prerequisites.")

        if item.execution_mode != "codex":
            raise OrchestratorError(
                f"Item {item_id} requested execution_mode={item.execution_mode!r}, "
                "but public core v1 supports only codex mutation stages."
            )
        if item.host_commands:
            raise OrchestratorError(
                f"Item {item_id} includes host_commands, but markdown_playbook_v1 reserves "
                "host-command execution for a future extension."
            )

        manager = WorktreeManager(self.repo_root, dirs.worktrees_root)
        attempt_number = self._next_attempt_number(item_state)
        item_state.attempt_number = attempt_number
        item_state.fix_rounds_completed = 0
        item_state.remediation_rounds_completed = 0
        item_state.terminal_state = "none"

        self._set_state(run_state, item_state, StateId.ST10_ITEM_SELECTED, "orchestrator", f"Selected item {item_id}.")

        control_dir = dirs.item_control_dir(item_id, attempt_number)
        report_dir = dirs.item_report_dir(item_id, attempt_number)
        ensure_directory(control_dir)
        ensure_directory(report_dir)

        if item.external_check.get("required") and not external_evidence_dir:
            return self._terminal_blocked_external(
                run_state=run_state,
                item_state=item_state,
                control_dir=control_dir,
                summary="Item requires human-supplied external evidence before prepare_context can continue.",
                artifact_manifest_path=item_state.latest_paths.artifact_manifest_path,
            )

        worktree = manager.prepare_item_worktree(
            run_id=run_state.run_id,
            item_id=item_id,
            attempt_number=attempt_number,
            run_branch_name=run_state.run_branch_name,
        )
        item_state.branch_name = worktree.branch_name
        item_state.worktree_path = worktree.path
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)

        self._set_state(run_state, item_state, StateId.ST15_WORKTREE_PREPARED, "git", f"Prepared worktree for item {item_id}.")

        worktree_path = resolve_repo_path(self.repo_root, worktree.path)
        packet_root = resolve_repo_path(self.repo_root, worktree.workspace_packet_root)

        external_evidence_copy = self._copy_external_evidence(
            source_dir=external_evidence_dir,
            destination_dir=control_dir / "external_evidence",
        )
        if external_evidence_copy:
            item_state.external_check_status = "resolved"
            touch_item_state(item_state)
            self._persist_active_run_state(run_state)

        previous_ref = manager.read_head_ref(worktree_path)
        current_stage_name = "execute"
        source_finding_ids: list[str] = []
        round_history: list[str] = []

        while True:
            try:
                mutation_report, previous_ref, verification_report = self._run_mutation_stage(
                    run_state=run_state,
                    item=item,
                    item_state=item_state,
                    worktree=worktree,
                    worktree_path=worktree_path,
                    packet_root=packet_root,
                    control_dir=control_dir,
                    report_dir=report_dir,
                    dirs=dirs,
                    stage_name=current_stage_name,
                    previous_ref=previous_ref,
                    source_finding_ids=source_finding_ids,
                    round_history=round_history,
                    external_evidence_copy=external_evidence_copy,
                )

                codex_audit, claude_audit, triage_report = self._run_audit_and_triage(
                    run_state=run_state,
                    item=item,
                    item_state=item_state,
                    worktree=worktree,
                    worktree_path=worktree_path,
                    packet_root=packet_root,
                    control_dir=control_dir,
                    report_dir=report_dir,
                    mutation_report=mutation_report,
                    verification_report=verification_report,
                    external_evidence_copy=external_evidence_copy,
                )
            except OrchestratorError:
                raise
            except Exception as exc:
                return self._raise_terminal_from_exception(
                    run_state=run_state,
                    item_state=item_state,
                    control_dir=control_dir,
                    summary=str(exc),
                )

            round_history.append(
                f"{current_stage_name}: {mutation_report['summary']} | triage={triage_report['overall_decision']}"
            )

            decision = triage_report["overall_decision"]
            if decision == "pass":
                if item.manual_gate.get("required"):
                    return self._terminal_awaiting_human_gate(
                        run_state=run_state,
                        item=item,
                        item_state=item_state,
                        control_dir=control_dir,
                    )
                return self._finalize_pass(
                    run_state=run_state,
                    item_state=item_state,
                    control_dir=control_dir,
                    summary=triage_report["summary"],
                )

            if decision == "awaiting_human_gate":
                return self._terminal_awaiting_human_gate(
                    run_state=run_state,
                    item=item,
                    item_state=item_state,
                    control_dir=control_dir,
                )

            if decision == "blocked_external":
                return self._terminal_blocked_external(
                    run_state=run_state,
                    item_state=item_state,
                    control_dir=control_dir,
                    summary=triage_report["summary"],
                    artifact_manifest_path=item_state.latest_paths.artifact_manifest_path,
                )

            if decision == "escalate":
                return self._terminal_escalated(
                    run_state=run_state,
                    item_state=item_state,
                    control_dir=control_dir,
                    summary=triage_report["summary"],
                    artifact_manifest_path=item_state.latest_paths.artifact_manifest_path,
                )

            actionable = [
                finding["canonical_id"]
                for finding in triage_report.get("merged_findings", [])
                if finding["disposition"] == "actionable"
            ]
            source_finding_ids = actionable

            if item_state.fix_rounds_completed < run_state.options.max_fix_rounds:
                item_state.fix_rounds_completed += 1
                self._set_state(
                    run_state,
                    item_state,
                    StateId.ST80_FIXING,
                    "codex",
                    f"Starting fix round {item_state.fix_rounds_completed}.",
                )
                current_stage_name = "fix"
                continue

            if item_state.remediation_rounds_completed < run_state.options.max_remediation_rounds:
                item_state.remediation_rounds_completed += 1
                self._set_state(
                    run_state,
                    item_state,
                    StateId.ST95_REMEDIATING,
                    "codex",
                    f"Starting remediation round {item_state.remediation_rounds_completed}.",
                )
                current_stage_name = "remediation"
                continue

            return self._terminal_escalated(
                run_state=run_state,
                item_state=item_state,
                control_dir=control_dir,
                summary="Actionable findings remained after the configured fix and remediation budgets were exhausted.",
                artifact_manifest_path=item_state.latest_paths.artifact_manifest_path,
            )

    def _build_item_context(
        self,
        *,
        run_state: RunState,
        item: Any,
        worktree: Any,
        stage_name: str,
        attempt_number: int,
        fix_round_index: int,
        remediation_round_index: int,
        prior_checkpoint_ref: str | None,
        artifact_specs: list[dict[str, Any]],
        support_sections: list[dict[str, Any]],
        artifact_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        verification_artifact_checks = [
            {
                "path": path,
                "check_kind": "exists",
                "expected_values": [],
                "reason": "Required deliverable artifact exists.",
            }
            for path in item.verification_hints.get("required_artifacts", [])
        ]
        command_groups = []
        required_commands = item.verification_hints.get("required_commands", [])
        if required_commands:
            command_groups.append(
                {
                    "label": "playbook_required",
                    "commands": required_commands,
                    "required": True,
                }
            )

        suggested_commands = item.verification_hints.get("suggested_commands", [])
        if suggested_commands:
            command_groups.append(
                {
                    "label": "playbook_suggested",
                    "commands": suggested_commands,
                    "required": False,
                }
            )

        artifact_inputs = self._item_context_artifact_inputs(
            artifact_specs=artifact_specs,
            worktree=worktree,
            artifact_manifest=artifact_manifest,
        )

        return ItemContext(
            schema_version="plan_orchestrator.item_context.v1",
            generated_at_utc=utc_now_iso(),
            run_id=run_state.run_id,
            adapter_id=run_state.adapter_id,
            item={
                "item_id": item.item_id,
                "phase": item.phase,
                "phase_slug": item.phase_slug,
                "action": item.action,
                "owner_type": item.owner_type,
                "deliverable": item.deliverable,
                "exit_criteria": item.exit_criteria,
                "change_profile": item.change_profile,
                "requires_red_green": item.requires_red_green,
                "manual_gate_required": bool(item.manual_gate.get("required")),
                "external_check_required": bool(item.external_check.get("required")),
            },
            worktree=worktree.to_dict(),
            stage_context={
                "stage": stage_name,
                "attempt_number": attempt_number,
                "fix_round_index": fix_round_index,
                "remediation_round_index": remediation_round_index,
                "prior_checkpoint_ref": prior_checkpoint_ref,
                "terminal_targets": [
                    "passed",
                    "awaiting_human_gate",
                    "blocked_external",
                    "escalated",
                ],
                "notes": item.notes,
            },
            repo_scope={
                "consult_paths": item.consult_paths,
                "allowed_write_roots": item.allowed_write_roots,
                "forbidden_roots": [
                    ".local",
                    ".git",
                    ".codex",
                    ".claude",
                    ".mcp.json",
                    "ops/config",
                    "secrets",
                ],
                "scope_notes": item.notes,
            },
            source_of_truth_paths=item.consult_paths,
            sensitive_path_globs=[
                ".env",
                ".env.*",
                "secrets/**",
                "ops/config/**",
                ".git/**",
            ],
            support_sections=support_sections,
            artifact_inputs=artifact_inputs,
            verification_plan={
                "command_groups": command_groups,
                "artifact_checks": verification_artifact_checks,
            },
        ).to_dict()

    def _item_context_artifact_inputs(
        self,
        *,
        artifact_specs: list[dict[str, Any]],
        worktree: Any,
        artifact_manifest: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if artifact_manifest is None:
            return [
                {
                    "logical_name": spec["logical_name"],
                    "path": repo_relative_path(self.repo_root, resolve_repo_path(self.repo_root, spec["path"])),
                    "workspace_path": workspace_path_for_artifact(
                        repo_root=self.repo_root,
                        worktree_root=resolve_repo_path(self.repo_root, worktree.path),
                        logical_name=spec["logical_name"],
                        path=spec["path"],
                        storage_class=spec["storage_class"],
                    ),
                    "sha256": None,
                    "required_for_stage": True,
                    "purpose": spec["description"],
                }
                for spec in artifact_specs
            ]

        manifest_entries = {
            entry["logical_name"]: entry
            for entry in artifact_manifest.get("artifacts", [])
        }
        artifact_inputs: list[dict[str, Any]] = []
        for spec in artifact_specs:
            entry = manifest_entries.get(spec["logical_name"])
            if entry is None:
                raise OrchestratorError(
                    f"Artifact manifest is missing stage input '{spec['logical_name']}'."
                )
            artifact_inputs.append(
                {
                    "logical_name": spec["logical_name"],
                    "path": entry["path"],
                    "workspace_path": (
                        entry["workspace_packet_path"]
                        if entry["workspace_packet_path"] is not None
                        else entry["path"]
                    ),
                    "sha256": entry["sha256"],
                    "required_for_stage": True,
                    "purpose": spec["description"],
                }
            )
        return artifact_inputs

    def _prepare_stage_files(
        self,
        *,
        run_state: RunState,
        item: Any,
        item_state: Any,
        worktree: Any,
        worktree_path: Path,
        packet_root: Path,
        control_dir: Path,
        stage_name: str,
        previous_ref: str | None,
        artifact_specs: list[dict[str, Any]],
        prompt_template_name: str,
        extra_prompt_values: dict[str, Any],
    ) -> tuple[Path, Path, dict[str, Any], str]:
        support_sections = self._item_context_support_sections(run_state=run_state, item=item)
        _, round_suffix, _ = self._stage_cycle_identity(item_state=item_state, stage_name=stage_name)

        context_data = self._build_item_context(
            run_state=run_state,
            item=item,
            worktree=worktree,
            stage_name=stage_name,
            attempt_number=item_state.attempt_number,
            fix_round_index=item_state.fix_rounds_completed,
            remediation_round_index=item_state.remediation_rounds_completed,
            prior_checkpoint_ref=previous_ref,
            artifact_specs=artifact_specs,
            support_sections=support_sections,
        )
        context_path = control_dir / f"item_context.{stage_name}.{round_suffix}.json"
        validate_named_schema("item_context.schema.json", context_data)
        write_json_atomic(context_path, context_data)

        all_artifacts = artifact_specs + [
            artifact_spec(
                logical_name=f"item_context_{stage_name}",
                path=context_path,
                content_type="json",
                storage_class="local_run_control",
                git_policy="gitignored",
                trust_level="orchestrator_generated",
                producer="prepare_context",
                consumers=[stage_name],
                must_exist=True,
                description=f"Stage context for {stage_name}.",
            )
        ]
        artifact_manifest_path = control_dir / f"artifact_manifest.{stage_name}.{round_suffix}.json"
        manifest, manifest_workspace = build_artifact_manifest(
            repo_root=self.repo_root,
            worktree_root=worktree_path,
            packet_root=packet_root,
            run_id=run_state.run_id,
            item_id=item.item_id,
            attempt_number=item_state.attempt_number,
            producer_stage=stage_name,
            artifact_specs=all_artifacts,
            output_path=artifact_manifest_path,
        )

        context_data = self._build_item_context(
            run_state=run_state,
            item=item,
            worktree=worktree,
            stage_name=stage_name,
            attempt_number=item_state.attempt_number,
            fix_round_index=item_state.fix_rounds_completed,
            remediation_round_index=item_state.remediation_rounds_completed,
            prior_checkpoint_ref=previous_ref,
            artifact_specs=artifact_specs,
            support_sections=support_sections,
            artifact_manifest=manifest,
        )
        validate_named_schema("item_context.schema.json", context_data)
        write_json_atomic(context_path, context_data)

        manifest, manifest_workspace = build_artifact_manifest(
            repo_root=self.repo_root,
            worktree_root=worktree_path,
            packet_root=packet_root,
            run_id=run_state.run_id,
            item_id=item.item_id,
            attempt_number=item_state.attempt_number,
            producer_stage=stage_name,
            artifact_specs=all_artifacts,
            output_path=artifact_manifest_path,
        )

        prompt_path = control_dir / "prompts" / f"{stage_name}.{round_suffix}.md"
        triage_report_workspace_path = extra_prompt_values.get("TRIAGE_REPORT_WORKSPACE_PATH", "")
        if not triage_report_workspace_path:
            try:
                triage_report_workspace_path = workspace_path_for_manifest_entry(manifest, "triage_report")
            except KeyError:
                triage_report_workspace_path = ""

        execution_or_fix_report_workspace_path = extra_prompt_values.get(
            "EXECUTION_OR_FIX_REPORT_WORKSPACE_PATH",
            "",
        )
        if not execution_or_fix_report_workspace_path:
            try:
                execution_or_fix_report_workspace_path = workspace_path_for_manifest_entry(
                    manifest,
                    "execution_or_fix_report",
                )
            except KeyError:
                execution_or_fix_report_workspace_path = ""

        variables = {
            "RUN_ID": run_state.run_id,
            "ITEM_ID": item.item_id,
            "ATTEMPT_NUMBER": item_state.attempt_number,
            "ITEM_PHASE": item.phase,
            "ITEM_ACTION": item.action,
            "ITEM_DELIVERABLE": item.deliverable,
            "ITEM_EXIT_CRITERIA": item.exit_criteria,
            "ITEM_CHANGE_PROFILE": item.change_profile,
            "ITEM_REQUIRES_RED_GREEN": str(item.requires_red_green).lower(),
            "WORKTREE_PATH": worktree.path,
            "WORKSPACE_PACKET_ROOT": worktree.workspace_packet_root,
            "ITEM_CONTEXT_WORKSPACE_PATH": workspace_path_for_manifest_entry(
                manifest,
                f"item_context_{stage_name}",
            ),
            "PLAYBOOK_SNAPSHOT_WORKSPACE_PATH": workspace_path_for_manifest_entry(manifest, "playbook_snapshot"),
            "NORMALIZED_PLAN_WORKSPACE_PATH": workspace_path_for_manifest_entry(manifest, "normalized_plan"),
            "CONSULT_PATHS_JSON": item.consult_paths,
            "ALLOWED_WRITE_ROOTS_JSON": item.allowed_write_roots,
            "FORBIDDEN_ROOTS_JSON": [
                ".local",
                ".git",
                ".codex",
                ".claude",
                ".mcp.json",
                "ops/config",
                "secrets",
            ],
            "ITEM_SUMMARY_JSON": item.to_dict(),
            "SUPPORT_SECTION_SUMMARY": "\n".join(
                f"- {section['title']} ({section['section_id']})"
                for section in support_sections
            ),
            "EXTERNAL_EVIDENCE_SUMMARY": "\n".join(
                f"- {entry['path']}"
                for entry in manifest["artifacts"]
                if entry["storage_class"] == "human_supplied_local"
            ) or "none",
            "AUDIT_PACKET_MANIFEST_WORKSPACE_PATH": ".local/plan_orchestrator/packet/audit_packet_manifest.json",
            "CANDIDATE_PATCH_WORKSPACE_PATH": ".local/plan_orchestrator/packet/artifacts/candidate_patch/candidate.patch",
            "VERIFICATION_REPORT_WORKSPACE_PATH": ".local/plan_orchestrator/packet/artifacts/verification_report/verification_report.json",
            "EXECUTION_OR_FIX_REPORT_WORKSPACE_PATH": execution_or_fix_report_workspace_path,
            "AUDIT_SCOPE_NOTES": extra_prompt_values.get("AUDIT_SCOPE_NOTES", ""),
            "ARTIFACT_MANIFEST_WORKSPACE_PATH": manifest_workspace,
            "MERGED_FINDINGS_WORKSPACE_PATH": extra_prompt_values.get("MERGED_FINDINGS_WORKSPACE_PATH", ""),
            "CODEX_AUDIT_REPORT_WORKSPACE_PATH": extra_prompt_values.get("CODEX_AUDIT_REPORT_WORKSPACE_PATH", ""),
            "CLAUDE_AUDIT_REPORT_WORKSPACE_PATH": extra_prompt_values.get("CLAUDE_AUDIT_REPORT_WORKSPACE_PATH", ""),
            "ITEM_MANUAL_GATE_REQUIRED": str(bool(item.manual_gate.get("required"))).lower(),
            "ITEM_EXTERNAL_CHECK_REQUIRED": str(bool(item.external_check.get("required"))).lower(),
            "TRIAGE_SCOPE_NOTES": extra_prompt_values.get("TRIAGE_SCOPE_NOTES", ""),
            "LOOP_ROUND": extra_prompt_values.get("LOOP_ROUND", 0),
            "TRIAGE_REPORT_WORKSPACE_PATH": triage_report_workspace_path,
            "SOURCE_FINDING_IDS_JSON": extra_prompt_values.get("SOURCE_FINDING_IDS_JSON", []),
            "ROUND_HISTORY_SUMMARY": extra_prompt_values.get("ROUND_HISTORY_SUMMARY", ""),
        }
        render_template(
            template_path=prompt_file(self.repo_root, prompt_template_name),
            output_path=prompt_path,
            variables=variables,
        )

        item_state.latest_paths.item_context_path = repo_relative_path(self.repo_root, context_path)
        item_state.latest_paths.artifact_manifest_path = repo_relative_path(self.repo_root, artifact_manifest_path)
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)
        return context_path, prompt_path, manifest, manifest_workspace

    def _item_context_support_sections(
        self,
        *,
        run_state: RunState,
        item: Any,
    ) -> list[dict[str, str]]:
        plan = NormalizedPlan.from_dict(
            load_json(resolve_repo_path(self.repo_root, run_state.normalized_plan_path))
        )
        projected_sections: list[dict[str, str]] = []
        for section in plan.support_sections_for_item(item):
            projected_sections.append(
                {
                    "section_id": section.section_id,
                    "title": section.title,
                    "body_markdown": section.body_markdown,
                    "why_included": self._support_section_reason(item=item, section=section),
                }
            )
        return projected_sections

    def _support_section_reason(self, *, item: Any, section: Any) -> str:
        reasons = {
            "global_context": "Global plan context that constrains how this item should be executed.",
            "phase_detail": f"Phase-specific guidance for {item.phase}.",
            "shared_guidance": "Shared guidance that applies across multiple items in the plan.",
            "risk_register": "Known risks and contingency guidance relevant to this item.",
            "informational": "Informational context attached for operator awareness.",
        }
        return reasons.get(
            section.section_kind,
            f"Relevant {section.section_kind.replace('_', ' ')} context for {item.phase}.",
        )

    def _run_mutation_stage(
        self,
        *,
        run_state: RunState,
        item: Any,
        item_state: Any,
        worktree: Any,
        worktree_path: Path,
        packet_root: Path,
        control_dir: Path,
        report_dir: Path,
        dirs: RunDirectories,
        stage_name: str,
        previous_ref: str,
        source_finding_ids: list[str],
        round_history: list[str],
        external_evidence_copy: Path | None,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        worktree.head_ref = previous_ref
        state_map = {
            "execute": StateId.ST20_CONTEXT_PREPARED,
            "fix": StateId.ST80_FIXING,
            "remediation": StateId.ST95_REMEDIATING,
        }
        if run_state.current_state != state_map[stage_name].value:
            self._set_state(
                run_state,
                item_state,
                state_map[stage_name],
                "orchestrator",
                f"Preparing {stage_name} context.",
            )

        base_specs = [
            artifact_spec(
                logical_name="playbook_snapshot",
                path=dirs.run_root / "playbook_source_snapshot.md",
                content_type="markdown",
                storage_class="local_run_control",
                git_policy="gitignored",
                trust_level="source_input",
                producer="normalize_plan",
                consumers=[stage_name],
                must_exist=True,
                description="Frozen copy of the source markdown playbook.",
            ),
            artifact_spec(
                logical_name="normalized_plan",
                path=resolve_repo_path(self.repo_root, run_state.normalized_plan_path),
                content_type="json",
                storage_class="local_run_control",
                git_policy="gitignored",
                trust_level="orchestrator_generated",
                producer="normalize_plan",
                consumers=[stage_name],
                must_exist=True,
                description="Normalized internal runtime manifest.",
            ),
            *self._tracked_repo_input_specs(item=item, consumer_stage=stage_name),
        ]
        if external_evidence_copy is not None:
            base_specs.append(
                artifact_spec(
                    logical_name="external_evidence",
                    path=external_evidence_copy,
                    content_type="directory",
                    storage_class="human_supplied_local",
                    git_policy="gitignored",
                    trust_level="human_supplied",
                    producer="human",
                    consumers=[stage_name],
                    must_exist=True,
                    description="Human-supplied external evidence for this item attempt.",
                )
            )

        if stage_name in {"fix", "remediation"}:
            triage_report_path = item_state.latest_paths.triage_report_path
            if not triage_report_path:
                raise OrchestratorError(
                    f"{stage_name} stage requires a triage report from the prior triage cycle."
                )
            base_specs.append(
                artifact_spec(
                    logical_name="triage_report",
                    path=resolve_repo_path(self.repo_root, triage_report_path),
                    content_type="json",
                    storage_class="local_model_report",
                    git_policy="gitignored",
                    trust_level="model_generated",
                    producer="codex",
                    consumers=[stage_name],
                    must_exist=True,
                    description="Most recent triage report for the current item attempt.",
                )
            )

        extra_prompt = {
            "LOOP_ROUND": (
                0
                if stage_name == "execute"
                else item_state.fix_rounds_completed
                if stage_name == "fix"
                else item_state.remediation_rounds_completed
            ),
            "SOURCE_FINDING_IDS_JSON": source_finding_ids,
            "ROUND_HISTORY_SUMMARY": "\n".join(f"- {line}" for line in round_history) or "none",
        }

        _, prompt_path, _, _ = self._prepare_stage_files(
            run_state=run_state,
            item=item,
            item_state=item_state,
            worktree=worktree,
            worktree_path=worktree_path,
            packet_root=packet_root,
            control_dir=control_dir,
            stage_name=stage_name,
            previous_ref=previous_ref,
            artifact_specs=base_specs,
            prompt_template_name=(
                "execution_codex.md"
                if stage_name == "execute"
                else "fix_codex.md"
                if stage_name == "fix"
                else "remediation_codex.md"
            ),
            extra_prompt_values=extra_prompt,
        )

        self._mark_mutation_stage_running(
            run_state=run_state,
            item_state=item_state,
            stage_name=stage_name,
        )
        _, round_suffix, cycle_tag = self._stage_cycle_identity(item_state=item_state, stage_name=stage_name)

        if stage_name == "execute":
            report_path = report_dir / f"execution_report.{round_suffix}.json"
        else:
            report_path = report_dir / f"{stage_name}_report.{round_suffix}.json"

        stdout_log = control_dir / "logs" / f"{stage_name}.{round_suffix}.stdout.log"
        stderr_log = control_dir / "logs" / f"{stage_name}.{round_suffix}.stderr.log"

        try:
            result = run_codex_stage(
                worktree_path=worktree_path,
                prompt_path=prompt_path,
                schema_path=schema_file(
                    self.repo_root,
                    "execution_report.schema.json" if stage_name == "execute" else "fix_report.schema.json",
                ),
                report_path=report_path,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                model=run_state.options.codex_model,
                reasoning_effort=run_state.options.codex_reasoning_effort,
                sandbox="workspace-write",
                timeout_sec=(
                    run_state.options.execution_timeout_sec
                    if stage_name == "execute"
                    else run_state.options.fix_timeout_sec
                    if stage_name == "fix"
                    else run_state.options.remediation_timeout_sec
                ),
            )
        except StageProcessError as exc:
            return self._raise_terminal_from_exception(
                run_state=run_state,
                item_state=item_state,
                control_dir=control_dir,
                summary=str(exc),
            )

        if stage_name == "execute":
            item_state.latest_paths.execution_report_path = repo_relative_path(self.repo_root, report_path)
        else:
            item_state.latest_paths.fix_report_path = repo_relative_path(self.repo_root, report_path)
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)

        try:
            stage_allowed_changes(
                worktree_path=worktree_path,
                allowed_write_roots=item.allowed_write_roots,
            )
            checkpoint_ref = create_checkpoint_commit(
                worktree_path=worktree_path,
                item_id=item.item_id,
                stage_name=stage_name,
            )
        except ScopeViolation as exc:
            return self._raise_terminal_from_exception(
                run_state=run_state,
                item_state=item_state,
                control_dir=control_dir,
                summary=str(exc),
            )

        item_state.checkpoint_ref = checkpoint_ref
        worktree.head_ref = checkpoint_ref
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)

        self._set_state(run_state, item_state, StateId.ST40_VERIFYING, "verification", "Running verification gate.")
        verification_report_path = control_dir / f"verification_report.{cycle_tag}.json"
        verification_report = run_verification(
            repo_root=self.repo_root,
            worktree_path=worktree_path,
            item_context=load_json(resolve_repo_path(self.repo_root, item_state.latest_paths.item_context_path)),
            previous_ref=previous_ref,
            current_ref=checkpoint_ref,
            report_path=verification_report_path,
            logs_dir=control_dir / "logs" / f"verification.{cycle_tag}",
            timeout_sec=run_state.options.verification_timeout_sec,
        )
        item_state.latest_paths.verification_report_path = repo_relative_path(self.repo_root, verification_report_path)
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)

        checkpoint_authority_violations = collect_post_checkpoint_authority_violations(
            worktree_path=worktree_path,
            checkpoint_ref=checkpoint_ref,
        )
        if checkpoint_authority_violations:
            return self._raise_terminal_from_exception(
                run_state=run_state,
                item_state=item_state,
                control_dir=control_dir,
                summary="; ".join(checkpoint_authority_violations),
            )

        if verification_report["scope_check"]["status"] == "fail":
            return self._raise_terminal_from_exception(
                run_state=run_state,
                item_state=item_state,
                control_dir=control_dir,
                summary=verification_report["summary"],
            )

        return result.report, checkpoint_ref, verification_report

    def _run_audit_and_triage(
        self,
        *,
        run_state: RunState,
        item: Any,
        item_state: Any,
        worktree: Any,
        worktree_path: Path,
        packet_root: Path,
        control_dir: Path,
        report_dir: Path,
        mutation_report: dict[str, Any],
        verification_report: dict[str, Any],
        external_evidence_copy: Path | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        try:
            self._set_state(run_state, item_state, StateId.ST50_AUDIT_PACKET_READY, "orchestrator", "Preparing audit packet.")
            _cycle_stage_name, _round_suffix, cycle_tag = self._current_cycle_identity(item_state=item_state)
            candidate_patch_path = control_dir / f"candidate.{cycle_tag}.patch"
            generate_patch(
                worktree_path=worktree_path,
                base_ref=worktree.base_ref,
                target_ref=item_state.checkpoint_ref or "HEAD",
                output_path=candidate_patch_path,
            )

            mutation_report_path = resolve_repo_path(
                self.repo_root,
                item_state.latest_paths.fix_report_path or item_state.latest_paths.execution_report_path or "",
            )
            verification_report_path = resolve_repo_path(
                self.repo_root,
                item_state.latest_paths.verification_report_path or "",
            )
            run_root = resolve_run_directories(self.repo_root, run_state.run_id).run_root

            audit_specs = [
                artifact_spec(
                    logical_name="playbook_snapshot",
                    path=run_root / "playbook_source_snapshot.md",
                    content_type="markdown",
                    storage_class="local_run_control",
                    git_policy="gitignored",
                    trust_level="source_input",
                    producer="normalize_plan",
                    consumers=["audit_codex", "audit_claude", "triage"],
                    must_exist=True,
                    description="Frozen copy of the source markdown playbook.",
                ),
                artifact_spec(
                    logical_name="normalized_plan",
                    path=resolve_repo_path(self.repo_root, run_state.normalized_plan_path),
                    content_type="json",
                    storage_class="local_run_control",
                    git_policy="gitignored",
                    trust_level="orchestrator_generated",
                    producer="normalize_plan",
                    consumers=["audit_codex", "audit_claude", "triage"],
                    must_exist=True,
                    description="Normalized internal runtime manifest.",
                ),
                artifact_spec(
                    logical_name="candidate_patch",
                    path=candidate_patch_path,
                    content_type="diff",
                    storage_class="local_run_control",
                    git_policy="gitignored",
                    trust_level="orchestrator_generated",
                    producer="git_checkpoint",
                    consumers=["audit_codex", "audit_claude", "triage"],
                    must_exist=True,
                    description="Patch from run-branch base to current checkpoint.",
                ),
                artifact_spec(
                    logical_name="verification_report",
                    path=verification_report_path,
                    content_type="json",
                    storage_class="local_run_control",
                    git_policy="gitignored",
                    trust_level="orchestrator_generated",
                    producer="verification",
                    consumers=["audit_codex", "audit_claude", "triage"],
                    must_exist=True,
                    description="Verification gate report.",
                ),
                artifact_spec(
                    logical_name="execution_or_fix_report",
                    path=mutation_report_path,
                    content_type="json",
                    storage_class="local_model_report",
                    git_policy="gitignored",
                    trust_level="model_generated",
                    producer="codex",
                    consumers=["audit_codex", "audit_claude", "triage"],
                    must_exist=True,
                    description="Latest mutation-stage report.",
                ),
            ]
            if external_evidence_copy is not None:
                audit_specs.append(
                    artifact_spec(
                        logical_name="external_evidence",
                        path=external_evidence_copy,
                        content_type="directory",
                        storage_class="human_supplied_local",
                        git_policy="gitignored",
                        trust_level="human_supplied",
                        producer="human",
                        consumers=["audit_codex", "audit_claude", "triage"],
                        must_exist=True,
                        description="Human-supplied external evidence for this item attempt.",
                    )
                )
            audit_manifest_path = control_dir / f"audit_packet_manifest.{cycle_tag}.json"
            audit_manifest, audit_manifest_workspace = build_artifact_manifest(
                repo_root=self.repo_root,
                worktree_root=worktree_path,
                packet_root=packet_root,
                run_id=run_state.run_id,
                item_id=item.item_id,
                attempt_number=item_state.attempt_number,
                producer_stage="audit_packet",
                artifact_specs=audit_specs,
                output_path=audit_manifest_path,
            )

            codex_prompt_path = control_dir / "prompts" / f"audit_codex.{cycle_tag}.md"
            render_template(
                template_path=prompt_file(self.repo_root, "audit_codex.md"),
                output_path=codex_prompt_path,
                variables={
                    "RUN_ID": run_state.run_id,
                    "ITEM_ID": item.item_id,
                    "ATTEMPT_NUMBER": item_state.attempt_number,
                    "WORKTREE_PATH": worktree.path,
                    "AUDIT_PACKET_MANIFEST_WORKSPACE_PATH": audit_manifest_workspace,
                    "CANDIDATE_PATCH_WORKSPACE_PATH": workspace_path_for_manifest_entry(audit_manifest, "candidate_patch"),
                    "VERIFICATION_REPORT_WORKSPACE_PATH": workspace_path_for_manifest_entry(audit_manifest, "verification_report"),
                    "EXECUTION_OR_FIX_REPORT_WORKSPACE_PATH": workspace_path_for_manifest_entry(audit_manifest, "execution_or_fix_report"),
                    "PLAYBOOK_SNAPSHOT_WORKSPACE_PATH": workspace_path_for_manifest_entry(audit_manifest, "playbook_snapshot"),
                    "NORMALIZED_PLAN_WORKSPACE_PATH": workspace_path_for_manifest_entry(audit_manifest, "normalized_plan"),
                    "AUDIT_SCOPE_NOTES": "Read-only audit over the frozen checkpoint and verification gate.",
                },
            )
            claude_prompt_path = control_dir / "prompts" / f"audit_claude.{cycle_tag}.md"
            render_template(
                template_path=prompt_file(self.repo_root, "audit_claude.md"),
                output_path=claude_prompt_path,
                variables={
                    "RUN_ID": run_state.run_id,
                    "ITEM_ID": item.item_id,
                    "ATTEMPT_NUMBER": item_state.attempt_number,
                    "WORKTREE_PATH": worktree.path,
                    "AUDIT_PACKET_MANIFEST_WORKSPACE_PATH": self._absolute_packet_path(
                        worktree_path=worktree_path,
                        workspace_packet_path=audit_manifest_workspace,
                    ),
                    "CANDIDATE_PATCH_WORKSPACE_PATH": self._absolute_packet_path(
                        worktree_path=worktree_path,
                        workspace_packet_path=workspace_path_for_manifest_entry(audit_manifest, "candidate_patch"),
                    ),
                    "VERIFICATION_REPORT_WORKSPACE_PATH": self._absolute_packet_path(
                        worktree_path=worktree_path,
                        workspace_packet_path=workspace_path_for_manifest_entry(audit_manifest, "verification_report"),
                    ),
                    "EXECUTION_OR_FIX_REPORT_WORKSPACE_PATH": self._absolute_packet_path(
                        worktree_path=worktree_path,
                        workspace_packet_path=workspace_path_for_manifest_entry(audit_manifest, "execution_or_fix_report"),
                    ),
                    "PLAYBOOK_SNAPSHOT_WORKSPACE_PATH": self._absolute_packet_path(
                        worktree_path=worktree_path,
                        workspace_packet_path=workspace_path_for_manifest_entry(audit_manifest, "playbook_snapshot"),
                    ),
                    "NORMALIZED_PLAN_WORKSPACE_PATH": self._absolute_packet_path(
                        worktree_path=worktree_path,
                        workspace_packet_path=workspace_path_for_manifest_entry(audit_manifest, "normalized_plan"),
                    ),
                    "AUDIT_SCOPE_NOTES": "Read-only audit over the frozen checkpoint and verification gate.",
                },
            )

            self._set_state(run_state, item_state, StateId.ST60_AUDITING_CODEX, "codex", "Running Codex audit.")
            codex_audit_path = report_dir / f"codex_audit_report.{cycle_tag}.json"
            codex_audit = run_codex_stage(
                worktree_path=worktree_path,
                prompt_path=codex_prompt_path,
                schema_path=schema_file(self.repo_root, "audit_report.schema.json"),
                report_path=codex_audit_path,
                stdout_log=control_dir / "logs" / f"audit_codex.{cycle_tag}.stdout.log",
                stderr_log=control_dir / "logs" / f"audit_codex.{cycle_tag}.stderr.log",
                model=run_state.options.codex_model,
                reasoning_effort=run_state.options.codex_reasoning_effort,
                sandbox="read-only",
                timeout_sec=run_state.options.audit_timeout_sec,
            ).report
            item_state.latest_paths.codex_audit_report_path = repo_relative_path(self.repo_root, codex_audit_path)
            touch_item_state(item_state)
            self._persist_active_run_state(run_state)

            self._set_state(run_state, item_state, StateId.ST61_AUDITING_CLAUDE, "claude", "Running Claude audit.")
            claude_audit_path = report_dir / f"claude_audit_report.{cycle_tag}.json"
            claude_audit = run_claude_audit(
                worktree_path=worktree_path,
                prompt_path=claude_prompt_path,
                schema_path=schema_file(self.repo_root, "audit_report.schema.json"),
                report_path=claude_audit_path,
                stderr_log=control_dir / "logs" / f"audit_claude.{cycle_tag}.stderr.log",
                item_id=item.item_id,
                attempt_number=item_state.attempt_number,
                model=run_state.options.claude_model,
                effort=run_state.options.claude_effort,
                max_turns=CLAUDE_MAX_TURNS_DEFAULT,
                timeout_sec=run_state.options.audit_timeout_sec,
            ).report
            item_state.latest_paths.claude_audit_report_path = repo_relative_path(self.repo_root, claude_audit_path)
            touch_item_state(item_state)
            self._persist_active_run_state(run_state)

            merged_findings_path = control_dir / f"merged_findings_packet.{cycle_tag}.json"
            merged_packet = write_merged_findings_packet(
                output_path=merged_findings_path,
                item_id=item.item_id,
                attempt_number=item_state.attempt_number,
                mutation_report=mutation_report,
                verification_report=verification_report,
                codex_audit_report=codex_audit,
                claude_audit_report=claude_audit,
            )

            triage_specs = audit_specs + [
                artifact_spec(
                    logical_name="codex_audit_report",
                    path=codex_audit_path,
                    content_type="json",
                    storage_class="local_model_report",
                    git_policy="gitignored",
                    trust_level="model_generated",
                    producer="codex",
                    consumers=["triage"],
                    must_exist=True,
                    description="Codex audit report.",
                ),
                artifact_spec(
                    logical_name="claude_audit_report",
                    path=claude_audit_path,
                    content_type="json",
                    storage_class="local_model_report",
                    git_policy="gitignored",
                    trust_level="model_generated",
                    producer="claude",
                    consumers=["triage"],
                    must_exist=True,
                    description="Claude audit report.",
                ),
                artifact_spec(
                    logical_name="merged_findings_packet",
                    path=merged_findings_path,
                    content_type="json",
                    storage_class="local_run_control",
                    git_policy="gitignored",
                    trust_level="orchestrator_generated",
                    producer="findings",
                    consumers=["triage"],
                    must_exist=True,
                    description="Deterministically merged findings packet.",
                ),
            ]
            triage_manifest_path = control_dir / f"artifact_manifest.triage.{cycle_tag}.json"
            triage_manifest, triage_manifest_workspace = build_artifact_manifest(
                repo_root=self.repo_root,
                worktree_root=worktree_path,
                packet_root=packet_root,
                run_id=run_state.run_id,
                item_id=item.item_id,
                attempt_number=item_state.attempt_number,
                producer_stage="triage_packet",
                artifact_specs=triage_specs,
                output_path=triage_manifest_path,
            )
            item_state.latest_paths.artifact_manifest_path = repo_relative_path(self.repo_root, triage_manifest_path)
            touch_item_state(item_state)
            self._persist_active_run_state(run_state)

            triage_prompt_path = control_dir / "prompts" / f"triage.{cycle_tag}.md"
            render_template(
                template_path=prompt_file(self.repo_root, "triage_codex.md"),
                output_path=triage_prompt_path,
                variables={
                    "RUN_ID": run_state.run_id,
                    "ITEM_ID": item.item_id,
                    "ATTEMPT_NUMBER": item_state.attempt_number,
                    "WORKTREE_PATH": worktree.path,
                    "ARTIFACT_MANIFEST_WORKSPACE_PATH": triage_manifest_workspace,
                    "MERGED_FINDINGS_WORKSPACE_PATH": workspace_path_for_manifest_entry(triage_manifest, "merged_findings_packet"),
                    "VERIFICATION_REPORT_WORKSPACE_PATH": workspace_path_for_manifest_entry(triage_manifest, "verification_report"),
                    "CODEX_AUDIT_REPORT_WORKSPACE_PATH": workspace_path_for_manifest_entry(triage_manifest, "codex_audit_report"),
                    "CLAUDE_AUDIT_REPORT_WORKSPACE_PATH": workspace_path_for_manifest_entry(triage_manifest, "claude_audit_report"),
                    "ITEM_MANUAL_GATE_REQUIRED": str(bool(item.manual_gate.get("required"))).lower(),
                    "ITEM_EXTERNAL_CHECK_REQUIRED": str(bool(item.external_check.get("required"))).lower(),
                    "TRIAGE_SCOPE_NOTES": "Merged findings were fingerprinted and deduped before triage.",
                },
            )

            self._set_state(run_state, item_state, StateId.ST70_TRIAGING, "codex", "Running triage stage.")
            triage_report_path = report_dir / f"triage_report.{cycle_tag}.json"
            triage_report = run_codex_stage(
                worktree_path=worktree_path,
                prompt_path=triage_prompt_path,
                schema_path=schema_file(self.repo_root, "triage_report.schema.json"),
                report_path=triage_report_path,
                stdout_log=control_dir / "logs" / f"triage.{cycle_tag}.stdout.log",
                stderr_log=control_dir / "logs" / f"triage.{cycle_tag}.stderr.log",
                model=run_state.options.codex_model,
                reasoning_effort=run_state.options.codex_reasoning_effort,
                sandbox="read-only",
                timeout_sec=run_state.options.triage_timeout_sec,
            ).report
            triage_report = self._preserve_mutation_control_handoff(
                item=item,
                triage_report=triage_report,
                triage_report_path=triage_report_path,
                merged_packet=merged_packet,
            )
            item_state.latest_paths.triage_report_path = repo_relative_path(self.repo_root, triage_report_path)
            touch_item_state(item_state)
            self._persist_active_run_state(run_state)

            return codex_audit, claude_audit, triage_report
        except OrchestratorError:
            raise
        except Exception as exc:
            return self._raise_terminal_from_exception(
                run_state=run_state,
                item_state=item_state,
                control_dir=control_dir,
                summary=str(exc),
            )

    def _raise_terminal_from_exception(
        self,
        *,
        run_state: RunState,
        item_state: Any,
        control_dir: Path,
        summary: str,
    ):
        self._terminal_escalated(
            run_state=run_state,
            item_state=item_state,
            control_dir=control_dir,
            summary=summary,
            artifact_manifest_path=item_state.latest_paths.artifact_manifest_path,
        )
        raise OrchestratorError(summary)

    def _terminal_awaiting_human_gate(
        self,
        *,
        run_state: RunState,
        item: Any,
        item_state: Any,
        control_dir: Path,
    ) -> str:
        self._set_state(run_state, item_state, StateId.ST110_AWAITING_HUMAN_GATE, "orchestrator", "Awaiting human gate.")
        item_state.terminal_state = "awaiting_human_gate"
        item_state.manual_gate_status = "pending"
        manual_gate_path = control_dir / "manual_gate.json"
        merged_findings_packet_path = self._latest_control_artifact(
            control_dir=control_dir,
            stem="merged_findings_packet",
        )
        write_manual_gate_record(
            output_path=manual_gate_path,
            run_id=run_state.run_id,
            item_id=item.item_id,
            gate_id=f"gate_{item.item_id}",
            gate_type=item.manual_gate["gate_type"],
            status="pending",
            requested_by="orchestrator",
            requested_reason=item.manual_gate["gate_reason"],
            required_evidence=item.manual_gate["required_evidence"],
            branch_name=item_state.branch_name,
            worktree_path=item_state.worktree_path,
            checkpoint_ref=item_state.checkpoint_ref,
            artifact_manifest_path=item_state.latest_paths.artifact_manifest_path or "",
            triage_report_path=item_state.latest_paths.triage_report_path,
            merged_findings_packet_path=repo_relative_path(self.repo_root, merged_findings_packet_path)
            if merged_findings_packet_path is not None
            else None,
            codex_audit_report_path=item_state.latest_paths.codex_audit_report_path,
            claude_audit_report_path=item_state.latest_paths.claude_audit_report_path,
            review_findings=self._manual_gate_review_findings(
                triage_report_path=item_state.latest_paths.triage_report_path,
            ),
            decision=None,
        )
        item_state.latest_paths.manual_gate_path = repo_relative_path(self.repo_root, manual_gate_path)
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)
        return "awaiting_human_gate"

    def _terminal_blocked_external(
        self,
        *,
        run_state: RunState,
        item_state: Any,
        control_dir: Path,
        summary: str,
        artifact_manifest_path: str | None,
    ) -> str:
        self._set_state(run_state, item_state, StateId.ST120_BLOCKED_EXTERNAL, "orchestrator", summary)
        item_state.terminal_state = "blocked_external"
        item_state.external_check_status = "pending_evidence"
        escalation_path = control_dir / "escalation_manifest.json"
        write_escalation_manifest(
            output_path=escalation_path,
            run_id=run_state.run_id,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state="blocked_external",
            summary=summary,
            blocking_reasons=[summary],
            required_human_actions=[
                {
                    "action_id": "supply_external_evidence",
                    "description": "Provide a valid external evidence directory and resume the blocked item.",
                    "owner_hint": "operator_external",
                    "evidence_needed": ["current human-supplied evidence files"],
                    "blocking": True,
                }
            ],
            branch_name=item_state.branch_name,
            worktree_path=item_state.worktree_path,
            checkpoint_ref=item_state.checkpoint_ref,
            run_state_path=repo_relative_path(
                self.repo_root,
                resolve_repo_path(self.repo_root, run_state.normalized_plan_path).parent / "run_state.json",
            ),
            primary_report_paths=[
                value
                for value in [
                    item_state.latest_paths.execution_report_path,
                    item_state.latest_paths.verification_report_path,
                    item_state.latest_paths.codex_audit_report_path,
                    item_state.latest_paths.claude_audit_report_path,
                    item_state.latest_paths.triage_report_path,
                ]
                if value
            ],
            artifact_manifest_path=artifact_manifest_path,
            suggested_resume_command=(
                f"python automation/run_plan_orchestrator.py resume --run-id {run_state.run_id} "
                "--external-evidence-dir /absolute/path/to/evidence"
            ),
            notes=[],
        )
        item_state.latest_paths.escalation_manifest_path = repo_relative_path(self.repo_root, escalation_path)
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)
        return "blocked_external"

    def _terminal_escalated(
        self,
        *,
        run_state: RunState,
        item_state: Any,
        control_dir: Path,
        summary: str,
        artifact_manifest_path: str | None,
    ) -> str:
        self._set_state(run_state, item_state, StateId.ST140_ESCALATED, "orchestrator", summary)
        item_state.terminal_state = "escalated"
        escalation_path = control_dir / "escalation_manifest.json"
        write_escalation_manifest(
            output_path=escalation_path,
            run_id=run_state.run_id,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state="escalated",
            summary=summary,
            blocking_reasons=[summary],
            required_human_actions=[
                {
                    "action_id": "inspect_terminal_bundle",
                    "description": "Inspect the escalation manifest, latest reports, and preserved worktree before deciding next action.",
                    "owner_hint": "human",
                    "evidence_needed": ["terminal bundle review"],
                    "blocking": True,
                }
            ],
            branch_name=item_state.branch_name,
            worktree_path=item_state.worktree_path,
            checkpoint_ref=item_state.checkpoint_ref,
            run_state_path=repo_relative_path(
                self.repo_root,
                resolve_repo_path(self.repo_root, run_state.normalized_plan_path).parent / "run_state.json",
            ),
            primary_report_paths=[
                value
                for value in [
                    item_state.latest_paths.execution_report_path,
                    item_state.latest_paths.verification_report_path,
                    item_state.latest_paths.codex_audit_report_path,
                    item_state.latest_paths.claude_audit_report_path,
                    item_state.latest_paths.triage_report_path,
                    item_state.latest_paths.fix_report_path,
                ]
                if value
            ],
            artifact_manifest_path=artifact_manifest_path,
            suggested_resume_command=f"python automation/run_plan_orchestrator.py resume --run-id {run_state.run_id}",
            notes=[],
        )
        item_state.latest_paths.escalation_manifest_path = repo_relative_path(self.repo_root, escalation_path)
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)
        return "escalated"

    def _finalize_pass(
        self,
        *,
        run_state: RunState,
        item_state: Any,
        control_dir: Path,
        summary: str,
    ) -> str:
        manager = WorktreeManager(
            self.repo_root,
            resolve_run_directories(self.repo_root, run_state.run_id).worktrees_root,
        )
        manager.fast_forward_run_branch(run_state.run_branch_name, item_state.branch_name or "")
        self._set_state(run_state, item_state, StateId.ST130_PASSED, "git", "Item passed and run branch fast-forwarded.")
        item_state.terminal_state = "passed"
        passed_summary_path = control_dir / "passed_summary.md"
        write_pass_summary(
            output_path=passed_summary_path,
            run_id=run_state.run_id,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            summary=summary,
            artifact_manifest_path=item_state.latest_paths.artifact_manifest_path or "",
            checkpoint_ref=item_state.checkpoint_ref,
        )
        touch_item_state(item_state)
        self._persist_active_run_state(run_state)
        return "passed"
