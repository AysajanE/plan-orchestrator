from __future__ import annotations

from enum import Enum

from .models import ItemRunState, PlanItem


class StateId(str, Enum):
    ST00_RUN_CREATED = "ST00_RUN_CREATED"
    ST05_PLAN_NORMALIZED = "ST05_PLAN_NORMALIZED"
    ST10_ITEM_SELECTED = "ST10_ITEM_SELECTED"
    ST15_WORKTREE_PREPARED = "ST15_WORKTREE_PREPARED"
    ST20_CONTEXT_PREPARED = "ST20_CONTEXT_PREPARED"
    ST30_EXECUTING = "ST30_EXECUTING"
    ST40_VERIFYING = "ST40_VERIFYING"
    ST50_AUDIT_PACKET_READY = "ST50_AUDIT_PACKET_READY"
    ST60_AUDITING_CODEX = "ST60_AUDITING_CODEX"
    ST61_AUDITING_CLAUDE = "ST61_AUDITING_CLAUDE"
    ST70_TRIAGING = "ST70_TRIAGING"
    ST80_FIXING = "ST80_FIXING"
    ST95_REMEDIATING = "ST95_REMEDIATING"
    ST110_AWAITING_HUMAN_GATE = "ST110_AWAITING_HUMAN_GATE"
    ST120_BLOCKED_EXTERNAL = "ST120_BLOCKED_EXTERNAL"
    ST130_PASSED = "ST130_PASSED"
    ST140_ESCALATED = "ST140_ESCALATED"


TERMINAL_ITEM_STATES = {
    "passed",
    "awaiting_human_gate",
    "blocked_external",
    "escalated",
}

TRANSITIONS: dict[StateId, set[StateId]] = {
    StateId.ST00_RUN_CREATED: {StateId.ST05_PLAN_NORMALIZED, StateId.ST140_ESCALATED},
    StateId.ST05_PLAN_NORMALIZED: {StateId.ST10_ITEM_SELECTED, StateId.ST140_ESCALATED},
    StateId.ST10_ITEM_SELECTED: {StateId.ST15_WORKTREE_PREPARED, StateId.ST120_BLOCKED_EXTERNAL},
    StateId.ST15_WORKTREE_PREPARED: {StateId.ST20_CONTEXT_PREPARED, StateId.ST140_ESCALATED},
    StateId.ST20_CONTEXT_PREPARED: {
        StateId.ST30_EXECUTING,
        StateId.ST120_BLOCKED_EXTERNAL,
        StateId.ST140_ESCALATED,
    },
    StateId.ST30_EXECUTING: {
        StateId.ST40_VERIFYING,
        StateId.ST120_BLOCKED_EXTERNAL,
        StateId.ST140_ESCALATED,
    },
    StateId.ST40_VERIFYING: {StateId.ST50_AUDIT_PACKET_READY, StateId.ST140_ESCALATED},
    StateId.ST50_AUDIT_PACKET_READY: {
        StateId.ST60_AUDITING_CODEX,
        StateId.ST61_AUDITING_CLAUDE,
        StateId.ST140_ESCALATED,
    },
    StateId.ST60_AUDITING_CODEX: {
        StateId.ST61_AUDITING_CLAUDE,
        StateId.ST70_TRIAGING,
        StateId.ST140_ESCALATED,
    },
    StateId.ST61_AUDITING_CLAUDE: {StateId.ST70_TRIAGING, StateId.ST140_ESCALATED},
    StateId.ST70_TRIAGING: {
        StateId.ST80_FIXING,
        StateId.ST95_REMEDIATING,
        StateId.ST110_AWAITING_HUMAN_GATE,
        StateId.ST120_BLOCKED_EXTERNAL,
        StateId.ST130_PASSED,
        StateId.ST140_ESCALATED,
    },
    StateId.ST80_FIXING: {
        StateId.ST40_VERIFYING,
        StateId.ST120_BLOCKED_EXTERNAL,
        StateId.ST140_ESCALATED,
    },
    StateId.ST95_REMEDIATING: {
        StateId.ST40_VERIFYING,
        StateId.ST120_BLOCKED_EXTERNAL,
        StateId.ST140_ESCALATED,
    },
    StateId.ST110_AWAITING_HUMAN_GATE: {StateId.ST130_PASSED, StateId.ST140_ESCALATED},
    StateId.ST120_BLOCKED_EXTERNAL: set(),
    StateId.ST130_PASSED: {StateId.ST10_ITEM_SELECTED, StateId.ST140_ESCALATED},
    StateId.ST140_ESCALATED: set(),
}


def assert_transition(current: str, target: str) -> None:
    current_state = StateId(current)
    target_state = StateId(target)
    allowed = TRANSITIONS[current_state]
    if target_state not in allowed:
        raise ValueError(f"Invalid state transition: {current_state.value} -> {target_state.value}")


def is_terminal_item_state_name(value: str) -> bool:
    return value in TERMINAL_ITEM_STATES


def state_for_terminal_name(value: str) -> StateId:
    mapping = {
        "passed": StateId.ST130_PASSED,
        "awaiting_human_gate": StateId.ST110_AWAITING_HUMAN_GATE,
        "blocked_external": StateId.ST120_BLOCKED_EXTERNAL,
        "escalated": StateId.ST140_ESCALATED,
    }
    return mapping[value]


def first_unfinished_item(
    plan_items: list[PlanItem],
    item_states: list[ItemRunState],
) -> PlanItem | None:
    by_id = {item.item_id: item for item in item_states}
    for item in sorted(plan_items, key=lambda candidate: candidate.order):
        state = by_id.get(item.item_id)
        if state is None:
            return item
        if state.terminal_state != "passed":
            return item
    return None


def prerequisites_satisfied(item: PlanItem, item_states: list[ItemRunState]) -> bool:
    by_id = {state.item_id: state for state in item_states}
    for prerequisite_id in item.prerequisite_item_ids:
        prerequisite = by_id.get(prerequisite_id)
        if prerequisite is None or prerequisite.terminal_state != "passed":
            return False
    return True
