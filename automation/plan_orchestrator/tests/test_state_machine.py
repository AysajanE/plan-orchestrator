from __future__ import annotations

import unittest

from automation.plan_orchestrator.models import ItemRunState, LatestPaths, PlanItem
from automation.plan_orchestrator.state_machine import (
    StateId,
    assert_transition,
    first_unfinished_item,
    is_terminal_item_state_name,
    prerequisites_satisfied,
    state_for_terminal_name,
)


def make_item(item_id: str, order: int, prereqs: list[str] | None = None) -> PlanItem:
    return PlanItem(
        item_id=item_id,
        order=order,
        phase=f"Phase {item_id}",
        phase_slug=f"phase-{item_id}",
        action="Do the thing.",
        why_now="Needed now.",
        owner_type="SWE",
        prerequisites_raw=",".join(prereqs or []) or "none",
        prerequisite_item_ids=list(prereqs or []),
        repo_surfaces_raw=["docs/runbooks/example.md"],
        repo_surface_paths=["docs/runbooks/example.md"],
        external_dependencies_raw=[],
        deliverable="docs/runbooks/example.md",
        deliverable_paths=["docs/runbooks/example.md"],
        exit_criteria="Artifact exists.",
        change_profile="docs_only",
        execution_mode="codex",
        host_commands=[],
        requires_red_green=False,
        manual_gate={"required": False, "gate_type": "none", "gate_reason": "", "required_evidence": []},
        external_check={"required": False, "mode": "none", "dependencies": []},
        verification_hints={
            "required_artifacts": [],
            "required_commands": [],
            "suggested_commands": [],
        },
        source_row={"line_start": 1, "line_end": 1, "raw_row_markdown": "| row |"},
        support_section_ids=[],
        consult_paths=["docs/runbooks/example.md"],
        allowed_write_roots=["docs/runbooks"],
        notes=[],
    )


def make_item_state(item_id: str, order: int, terminal_state: str) -> ItemRunState:
    return ItemRunState(
        item_id=item_id,
        order=order,
        state="ST05_PLAN_NORMALIZED",
        attempt_number=1,
        fix_rounds_completed=0,
        remediation_rounds_completed=0,
        manual_gate_status="not_required",
        external_check_status="not_required",
        branch_name=None,
        worktree_path=None,
        checkpoint_ref=None,
        terminal_state=terminal_state,
        latest_paths=LatestPaths(),
        updated_at_utc="2026-03-14T00:00:00Z",
    )


class StateMachineTests(unittest.TestCase):
    def test_assert_transition_allows_valid_edge_and_rejects_invalid_one(self) -> None:
        assert_transition(StateId.ST00_RUN_CREATED.value, StateId.ST05_PLAN_NORMALIZED.value)
        assert_transition(StateId.ST60_AUDITING_CODEX.value, StateId.ST61_AUDITING_CLAUDE.value)

        with self.assertRaises(ValueError):
            assert_transition(StateId.ST00_RUN_CREATED.value, StateId.ST80_FIXING.value)

    def test_terminal_name_helpers_map_expected_states(self) -> None:
        self.assertTrue(is_terminal_item_state_name("passed"))
        self.assertFalse(is_terminal_item_state_name("none"))
        self.assertEqual(state_for_terminal_name("blocked_external"), StateId.ST120_BLOCKED_EXTERNAL)

    def test_first_unfinished_item_returns_first_non_passed_item(self) -> None:
        items = [make_item("01", 1), make_item("02", 2), make_item("03", 3)]
        states = [
            make_item_state("01", 1, "passed"),
            make_item_state("02", 2, "awaiting_human_gate"),
            make_item_state("03", 3, "none"),
        ]

        unfinished = first_unfinished_item(items, states)

        self.assertIsNotNone(unfinished)
        self.assertEqual(unfinished.item_id, "02")

    def test_prerequisites_satisfied_requires_all_prerequisites_to_be_passed(self) -> None:
        item = make_item("06", 6, prereqs=["04", "05"])
        states = [
            make_item_state("04", 4, "passed"),
            make_item_state("05", 5, "passed"),
        ]

        self.assertTrue(prerequisites_satisfied(item, states))

        states[1].terminal_state = "awaiting_human_gate"
        self.assertFalse(prerequisites_satisfied(item, states))
