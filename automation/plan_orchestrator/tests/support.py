from __future__ import annotations

import subprocess
from pathlib import Path

from automation.plan_orchestrator.models import NormalizedPlan, PlanItem, RuntimeOptions


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


def make_plan(items: list[PlanItem] | None = None) -> NormalizedPlan:
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
        items=items or [make_item("01", 1)],
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


def write_minimal_playbook(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "## 1. Goal",
                "",
                "Ship the change safely.",
                "",
                "## 2. Ordered Execution Plan",
                "",
                "| step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| 01 | Docs | Update the runbook. | Need an operator proof point. | operator | none | `docs/runbooks/policy_note.md` | `docs/runbooks/policy_note.md` | Artifact exists. | docs/runbooks | no |",
            ]
        )
        + "\n",
        encoding="utf-8",
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
