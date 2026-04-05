from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_store import load_run_state
from .supervision_artifacts import (
    DEFAULT_MAX_AUTO_RESUME_ATTEMPTS,
    SupervisionPaths,
    list_intervention_paths,
    load_intervention,
    resolve_supervision_paths,
)
from .validators import compute_path_sha256, load_json, resolve_repo_path


@dataclass(frozen=True)
class RecoveryDecision:
    action_kind: str
    recoverability_class: str
    reason: str
    fingerprint: str | None
    item_id: str | None
    attempt_number: int | None
    terminal_state: str | None
    pending_action_kind: str | None
    next_supervisor_action: str
    evidence_directory: str | None = None
    evidence_package_sha256: str | None = None


def _resolve_local_directory(repo_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path


def _ready_evidence_directory(repo_root: Path, explicit_dir: str | None, inbox_dir: str | None) -> Path | None:
    for raw in (explicit_dir, inbox_dir):
        candidate = _resolve_local_directory(repo_root, raw)
        if candidate is None:
            continue
        if candidate.exists() and candidate.is_dir():
            try:
                next(candidate.iterdir())
            except StopIteration:
                continue
            return candidate
    return None


def _current_item_state(run_state) -> Any | None:
    if run_state.current_item_id:
        return run_state.get_item_state(run_state.current_item_id)
    for item_state in sorted(run_state.items, key=lambda value: value.order):
        if item_state.terminal_state != "passed":
            return item_state
    return None


def _has_unfinished_items(run_state) -> bool:
    return any(item_state.terminal_state != "passed" for item_state in run_state.items)


def _latest_report_sha(repo_root: Path, rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    path = resolve_repo_path(repo_root, rel_path)
    return compute_path_sha256(path)


def _doctor_run_references(doctor_report: dict[str, Any]) -> dict[str, Any]:
    for check in doctor_report.get("checks", []):
        if check.get("name") == "run_references":
            return check
    return {}


def _manual_gate_rejected(repo_root: Path, item_state) -> bool:
    manual_gate_path = item_state.latest_paths.manual_gate_path
    if not manual_gate_path:
        return False
    path = resolve_repo_path(repo_root, manual_gate_path)
    if not path.exists():
        return False
    payload = load_json(path)
    return payload.get("status") == "rejected"


def _verification_scope_failed(repo_root: Path, item_state) -> bool:
    verification_path = item_state.latest_paths.verification_report_path
    if not verification_path:
        return False
    path = resolve_repo_path(repo_root, verification_path)
    if not path.exists():
        return False
    payload = load_json(path)
    return payload.get("scope_check", {}).get("status") == "fail"


def _needs_doctor_fix(run_references: dict[str, Any]) -> bool:
    checks = run_references.get("checks", {})
    return (
        checks.get("normalized_plan_path_exists") is False
        or checks.get("normalized_plan_valid") is False
    )


def build_recovery_fingerprint(
    *,
    repo_root: Path,
    run_state,
    item_state,
    status_summary: dict[str, Any],
    evidence_package_sha256: str | None,
) -> str:
    summary_text = ""
    pending_action = status_summary.get("pending_action") or {}
    if pending_action:
        summary_text = str(pending_action.get("detail", "") or "")
    verification_sha = _latest_report_sha(repo_root, item_state.latest_paths.verification_report_path)
    triage_sha = _latest_report_sha(repo_root, item_state.latest_paths.triage_report_path)
    fix_or_exec_sha = _latest_report_sha(
        repo_root,
        item_state.latest_paths.fix_report_path or item_state.latest_paths.execution_report_path,
    )
    payload = {
        "run_id": run_state.run_id,
        "item_id": item_state.item_id,
        "terminal_state": item_state.terminal_state,
        "current_state": run_state.current_state,
        "summary": summary_text,
        "verification_sha256": verification_sha,
        "triage_sha256": triage_sha,
        "fix_or_execution_sha256": fix_or_exec_sha,
        "evidence_package_sha256": evidence_package_sha256,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _count_prior_interventions(
    paths: SupervisionPaths,
    *,
    action_kind: str,
    fingerprint: str | None,
) -> int:
    if fingerprint is None:
        return 0
    count = 0
    for path in list_intervention_paths(paths):
        payload = load_intervention(path)
        if payload.get("action_kind") != action_kind:
            continue
        if payload.get("fingerprint") == fingerprint:
            count += 1
    return count


def classify_recovery(
    *,
    repo_root: Path,
    run_id: str,
    status_summary: dict[str, Any],
    doctor_report: dict[str, Any],
    evidence_inbox_dir: str | None,
    explicit_external_evidence_dir: str | None,
    max_auto_resume_attempts: int | None,
    prior_wait_action_kind: str | None,
    initial_resume_requested: bool,
    allow_resume_after_manual_gate: bool,
) -> RecoveryDecision:
    run_state = load_run_state(resolve_supervision_paths(repo_root, run_id).run_root / "run_state.json")
    paths = resolve_supervision_paths(repo_root, run_id)
    item_state = _current_item_state(run_state)
    pending_action = status_summary.get("pending_action") or {}
    pending_action_kind = pending_action.get("kind")

    if item_state is None:
        return RecoveryDecision(
            action_kind="terminal_passed",
            recoverability_class="passed",
            reason="All items are already passed.",
            fingerprint=None,
            item_id=None,
            attempt_number=None,
            terminal_state="passed",
            pending_action_kind=pending_action_kind,
            next_supervisor_action="terminal_observed",
        )

    ready_evidence_dir = _ready_evidence_directory(
        repo_root,
        explicit_external_evidence_dir,
        evidence_inbox_dir,
    )
    ready_evidence_sha = compute_path_sha256(ready_evidence_dir) if ready_evidence_dir else None
    run_references = _doctor_run_references(doctor_report)

    if (
        prior_wait_action_kind == "wait_manual_gate"
        and allow_resume_after_manual_gate
        and run_state.current_state == "ST130_PASSED"
        and _has_unfinished_items(run_state)
    ):
        return RecoveryDecision(
            action_kind="resume_after_manual_gate",
            recoverability_class="recoverable",
            reason="A human approval was recorded and run-level continuation remains truthful.",
            fingerprint=None,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state=item_state.terminal_state,
            pending_action_kind=pending_action_kind,
            next_supervisor_action="resume",
        )

    if run_state.current_state == "ST110_AWAITING_HUMAN_GATE" or item_state.terminal_state == "awaiting_human_gate":
        return RecoveryDecision(
            action_kind="wait_manual_gate",
            recoverability_class="waiting",
            reason="The current item is still awaiting a human gate decision.",
            fingerprint=None,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state="awaiting_human_gate",
            pending_action_kind="manual_gate",
            next_supervisor_action="wait",
        )

    if run_state.current_state == "ST120_BLOCKED_EXTERNAL" or item_state.terminal_state == "blocked_external":
        fingerprint = build_recovery_fingerprint(
            repo_root=repo_root,
            run_state=run_state,
            item_state=item_state,
            status_summary=status_summary,
            evidence_package_sha256=ready_evidence_sha,
        )
        if ready_evidence_dir is None:
            return RecoveryDecision(
                action_kind="wait_external_evidence",
                recoverability_class="waiting",
                reason="The current item is still blocked on human-supplied local external evidence.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state="blocked_external",
                pending_action_kind="external_evidence",
                next_supervisor_action="wait",
            )
        if _count_prior_interventions(paths, action_kind="resume_blocked_external", fingerprint=fingerprint) >= 1:
            return RecoveryDecision(
                action_kind="park",
                recoverability_class="non_recoverable",
                reason="The same blocked_external fingerprint already retried this evidence package once.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state="blocked_external",
                pending_action_kind="external_evidence",
                next_supervisor_action="park",
                evidence_directory=ready_evidence_dir.as_posix(),
                evidence_package_sha256=ready_evidence_sha,
            )
        return RecoveryDecision(
            action_kind="resume_blocked_external",
            recoverability_class="recoverable",
            reason="Valid local evidence is present for the blocked item.",
            fingerprint=fingerprint,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state="blocked_external",
            pending_action_kind="external_evidence",
            next_supervisor_action="resume",
            evidence_directory=ready_evidence_dir.as_posix(),
            evidence_package_sha256=ready_evidence_sha,
        )

    if pending_action_kind == "repair_local_state" and _needs_doctor_fix(run_references):
        fingerprint = build_recovery_fingerprint(
            repo_root=repo_root,
            run_state=run_state,
            item_state=item_state,
            status_summary=status_summary,
            evidence_package_sha256=None,
        )
        if _count_prior_interventions(paths, action_kind="doctor_fix_safe", fingerprint=fingerprint) >= 1:
            return RecoveryDecision(
                action_kind="park",
                recoverability_class="non_recoverable",
                reason="A deterministic local repair was already attempted for the current fingerprint.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state=item_state.terminal_state,
                pending_action_kind="repair_local_state",
                next_supervisor_action="park",
            )
        return RecoveryDecision(
            action_kind="doctor_fix_safe",
            recoverability_class="recoverable",
            reason="Deterministic local orchestrator drift is repairable with doctor --fix-safe.",
            fingerprint=fingerprint,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state=item_state.terminal_state,
            pending_action_kind="repair_local_state",
            next_supervisor_action="doctor_fix_safe",
        )

    if run_state.current_state == "ST140_ESCALATED" or item_state.terminal_state == "escalated":
        fingerprint = build_recovery_fingerprint(
            repo_root=repo_root,
            run_state=run_state,
            item_state=item_state,
            status_summary=status_summary,
            evidence_package_sha256=None,
        )
        if _manual_gate_rejected(repo_root, item_state):
            return RecoveryDecision(
                action_kind="park",
                recoverability_class="non_recoverable",
                reason="The current escalated case originated from a manual-gate rejection.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state="escalated",
                pending_action_kind="escalated",
                next_supervisor_action="park",
            )
        if run_references.get("checks", {}).get("run_branch_exists") is False:
            return RecoveryDecision(
                action_kind="park",
                recoverability_class="non_recoverable",
                reason="The run branch is missing and the current kernel cannot truthfully reconstruct that history automatically.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state="escalated",
                pending_action_kind="escalated",
                next_supervisor_action="park",
            )
        if _verification_scope_failed(repo_root, item_state):
            return RecoveryDecision(
                action_kind="park",
                recoverability_class="non_recoverable",
                reason="Verification scope failed; this is not safe to auto-resume.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state="escalated",
                pending_action_kind="escalated",
                next_supervisor_action="park",
            )
        if _needs_doctor_fix(run_references):
            if _count_prior_interventions(paths, action_kind="doctor_fix_safe", fingerprint=fingerprint) >= 1:
                return RecoveryDecision(
                    action_kind="park",
                    recoverability_class="non_recoverable",
                    reason="Deterministic local repair was already attempted for this escalated fingerprint.",
                    fingerprint=fingerprint,
                    item_id=item_state.item_id,
                    attempt_number=item_state.attempt_number,
                    terminal_state="escalated",
                    pending_action_kind="repair_local_state",
                    next_supervisor_action="park",
                )
            return RecoveryDecision(
                action_kind="doctor_fix_safe",
                recoverability_class="recoverable",
                reason="The escalated case includes deterministic local drift that is repairable with doctor --fix-safe.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state="escalated",
                pending_action_kind="repair_local_state",
                next_supervisor_action="doctor_fix_safe",
            )

        retry_budget = DEFAULT_MAX_AUTO_RESUME_ATTEMPTS if max_auto_resume_attempts is None else max_auto_resume_attempts
        if _count_prior_interventions(paths, action_kind="resume_escalated", fingerprint=fingerprint) >= retry_budget:
            return RecoveryDecision(
                action_kind="park",
                recoverability_class="non_recoverable",
                reason="The bounded escalated auto-resume budget is exhausted for this fingerprint.",
                fingerprint=fingerprint,
                item_id=item_state.item_id,
                attempt_number=item_state.attempt_number,
                terminal_state="escalated",
                pending_action_kind="escalated",
                next_supervisor_action="park",
            )

        return RecoveryDecision(
            action_kind="resume_escalated",
            recoverability_class="recoverable",
            reason="The current escalated fingerprint is inside the bounded automatic resume budget.",
            fingerprint=fingerprint,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state="escalated",
            pending_action_kind="escalated",
            next_supervisor_action="resume",
        )

    if initial_resume_requested and _has_unfinished_items(run_state):
        return RecoveryDecision(
            action_kind="resume_saved_run",
            recoverability_class="recoverable",
            reason="A saved run was explicitly re-entered under supervision.",
            fingerprint=None,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state=item_state.terminal_state,
            pending_action_kind=pending_action_kind,
            next_supervisor_action="resume",
        )

    if run_state.current_state == "ST130_PASSED":
        return RecoveryDecision(
            action_kind="terminal_passed",
            recoverability_class="passed",
            reason="The supervised invocation reached a passed terminal.",
            fingerprint=None,
            item_id=item_state.item_id,
            attempt_number=item_state.attempt_number,
            terminal_state=item_state.terminal_state,
            pending_action_kind=pending_action_kind,
            next_supervisor_action="terminal_observed",
        )

    return RecoveryDecision(
        action_kind="park",
        recoverability_class="non_recoverable",
        reason="The current state does not match a truthful automatic recovery path.",
        fingerprint=None,
        item_id=item_state.item_id,
        attempt_number=item_state.attempt_number,
        terminal_state=item_state.terminal_state,
        pending_action_kind=pending_action_kind,
        next_supervisor_action="park",
    )
