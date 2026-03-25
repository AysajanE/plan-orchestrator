from __future__ import annotations

from pathlib import Path

from .models import (
    ItemRunState,
    LatestPaths,
    NormalizedPlan,
    RunEvent,
    RunState,
    RuntimeOptions,
)
from .validators import utc_now_iso, validate_named_schema, write_json_atomic, load_json


def empty_latest_paths() -> LatestPaths:
    return LatestPaths()


def create_run_state(
    *,
    run_id: str,
    adapter_id: str,
    repo_root: str,
    playbook_source_path: str,
    playbook_source_sha256: str,
    normalized_plan_path: str,
    base_head_sha: str,
    run_branch_name: str,
    options: RuntimeOptions,
    plan: NormalizedPlan,
) -> RunState:
    now = utc_now_iso()
    item_states: list[ItemRunState] = []
    for item in plan.items:
        external_check_status = (
            "pending_evidence" if item.external_check.get("required", False) else "not_required"
        )
        item_states.append(
            ItemRunState(
                item_id=item.item_id,
                order=item.order,
                state="ST05_PLAN_NORMALIZED",
                attempt_number=1,
                fix_rounds_completed=0,
                remediation_rounds_completed=0,
                manual_gate_status="not_required",
                external_check_status=external_check_status,
                branch_name=None,
                worktree_path=None,
                checkpoint_ref=None,
                terminal_state="none",
                latest_paths=empty_latest_paths(),
                updated_at_utc=now,
            )
        )

    return RunState(
        schema_version="plan_orchestrator.run_state.v1",
        run_id=run_id,
        adapter_id=adapter_id,
        repo_root=repo_root,
        playbook_source_path=playbook_source_path,
        playbook_source_sha256=playbook_source_sha256,
        normalized_plan_path=normalized_plan_path,
        base_head_sha=base_head_sha,
        run_branch_name=run_branch_name,
        created_at_utc=now,
        updated_at_utc=now,
        current_state="ST00_RUN_CREATED",
        current_item_id=None,
        options=options,
        items=item_states,
        event_log=[],
    )


def load_run_state(path: Path) -> RunState:
    data = load_json(path)
    validate_named_schema("run_state.schema.json", data)
    return RunState.from_dict(data)


def save_run_state(path: Path, state: RunState) -> None:
    state.updated_at_utc = utc_now_iso()
    payload = state.to_dict()
    validate_named_schema("run_state.schema.json", payload)
    write_json_atomic(path, payload)


def append_event(state: RunState, *, actor: str, message: str) -> None:
    state.event_log.append(
        RunEvent(
            at_utc=utc_now_iso(),
            state=state.current_state,
            actor=actor,
            message=message,
        )
    )


def touch_item_state(item_state: ItemRunState) -> None:
    item_state.updated_at_utc = utc_now_iso()
