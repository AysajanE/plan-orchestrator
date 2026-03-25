from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class SupportSection:
    section_id: str
    title: str
    section_kind: str
    body_markdown: str
    applies_to_phase_slugs: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupportSection":
        return cls(
            section_id=data["section_id"],
            title=data["title"],
            section_kind=data["section_kind"],
            body_markdown=data["body_markdown"],
            applies_to_phase_slugs=list(data.get("applies_to_phase_slugs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanItem:
    item_id: str
    order: int
    phase: str
    phase_slug: str
    action: str
    why_now: str
    owner_type: str
    prerequisites_raw: str
    prerequisite_item_ids: list[str]
    repo_surfaces_raw: list[str]
    repo_surface_paths: list[str]
    external_dependencies_raw: list[str]
    deliverable: str
    deliverable_paths: list[str]
    exit_criteria: str
    change_profile: str
    execution_mode: str
    host_commands: list[str]
    requires_red_green: bool
    manual_gate: dict[str, Any]
    external_check: dict[str, Any]
    verification_hints: dict[str, Any]
    source_row: dict[str, Any]
    support_section_ids: list[str]
    consult_paths: list[str]
    allowed_write_roots: list[str]
    notes: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanItem":
        return cls(
            item_id=data["item_id"],
            order=int(data["order"]),
            phase=data["phase"],
            phase_slug=data["phase_slug"],
            action=data["action"],
            why_now=data["why_now"],
            owner_type=data["owner_type"],
            prerequisites_raw=data["prerequisites_raw"],
            prerequisite_item_ids=list(data.get("prerequisite_item_ids", [])),
            repo_surfaces_raw=list(data.get("repo_surfaces_raw", [])),
            repo_surface_paths=list(data.get("repo_surface_paths", [])),
            external_dependencies_raw=list(data.get("external_dependencies_raw", [])),
            deliverable=data["deliverable"],
            deliverable_paths=list(data.get("deliverable_paths", [])),
            exit_criteria=data["exit_criteria"],
            change_profile=data["change_profile"],
            execution_mode=data.get("execution_mode", "codex"),
            host_commands=list(data.get("host_commands", [])),
            requires_red_green=bool(data["requires_red_green"]),
            manual_gate=dict(data["manual_gate"]),
            external_check=dict(data["external_check"]),
            verification_hints=dict(data["verification_hints"]),
            source_row=dict(data["source_row"]),
            support_section_ids=list(data.get("support_section_ids", [])),
            consult_paths=list(data.get("consult_paths", [])),
            allowed_write_roots=list(data.get("allowed_write_roots", [])),
            notes=list(data.get("notes", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedPlan:
    schema_version: str
    adapter_id: str
    plan_source: dict[str, Any]
    generated_at_utc: str
    global_context: dict[str, Any]
    support_sections: list[SupportSection]
    items: list[PlanItem]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NormalizedPlan":
        return cls(
            schema_version=data["schema_version"],
            adapter_id=data["adapter_id"],
            plan_source=dict(data["plan_source"]),
            generated_at_utc=data["generated_at_utc"],
            global_context=dict(data["global_context"]),
            support_sections=[
                SupportSection.from_dict(section)
                for section in data.get("support_sections", [])
            ],
            items=[PlanItem.from_dict(item) for item in data.get("items", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "plan_source": self.plan_source,
            "generated_at_utc": self.generated_at_utc,
            "global_context": self.global_context,
            "support_sections": [section.to_dict() for section in self.support_sections],
            "items": [item.to_dict() for item in self.items],
        }

    def get_item(self, item_id: str) -> PlanItem:
        for item in self.items:
            if item.item_id == item_id:
                return item
        raise KeyError(f"Unknown item_id: {item_id}")

    def support_sections_for_item(self, item: PlanItem) -> list[SupportSection]:
        wanted = set(item.support_section_ids)
        return [section for section in self.support_sections if section.section_id in wanted]


@dataclass
class RuntimeOptions:
    codex_model: str
    codex_reasoning_effort: str
    claude_model: str
    claude_effort: str
    auto_advance: bool
    max_items: Optional[int]
    max_fix_rounds: int
    max_remediation_rounds: int
    execution_timeout_sec: int
    verification_timeout_sec: int
    audit_timeout_sec: int
    triage_timeout_sec: int
    fix_timeout_sec: int
    remediation_timeout_sec: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeOptions":
        return cls(
            codex_model=data["codex_model"],
            codex_reasoning_effort=data["codex_reasoning_effort"],
            claude_model=data["claude_model"],
            claude_effort=data["claude_effort"],
            auto_advance=bool(data["auto_advance"]),
            max_items=data.get("max_items"),
            max_fix_rounds=int(data["max_fix_rounds"]),
            max_remediation_rounds=int(data["max_remediation_rounds"]),
            execution_timeout_sec=int(data["execution_timeout_sec"]),
            verification_timeout_sec=int(
                data.get("verification_timeout_sec", data["execution_timeout_sec"])
            ),
            audit_timeout_sec=int(data["audit_timeout_sec"]),
            triage_timeout_sec=int(data["triage_timeout_sec"]),
            fix_timeout_sec=int(data["fix_timeout_sec"]),
            remediation_timeout_sec=int(data["remediation_timeout_sec"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunEvent:
    at_utc: str
    state: str
    actor: str
    message: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunEvent":
        return cls(
            at_utc=data["at_utc"],
            state=data["state"],
            actor=data["actor"],
            message=data["message"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LatestPaths:
    item_context_path: Optional[str] = None
    execution_report_path: Optional[str] = None
    verification_report_path: Optional[str] = None
    codex_audit_report_path: Optional[str] = None
    claude_audit_report_path: Optional[str] = None
    triage_report_path: Optional[str] = None
    fix_report_path: Optional[str] = None
    artifact_manifest_path: Optional[str] = None
    manual_gate_path: Optional[str] = None
    escalation_manifest_path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LatestPaths":
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ItemRunState:
    item_id: str
    order: int
    state: str
    attempt_number: int
    fix_rounds_completed: int
    remediation_rounds_completed: int
    manual_gate_status: str
    external_check_status: str
    branch_name: Optional[str]
    worktree_path: Optional[str]
    checkpoint_ref: Optional[str]
    terminal_state: str
    latest_paths: LatestPaths
    updated_at_utc: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ItemRunState":
        latest_paths = data.get("latest_paths", {})
        return cls(
            item_id=data["item_id"],
            order=int(data["order"]),
            state=data["state"],
            attempt_number=int(data["attempt_number"]),
            fix_rounds_completed=int(data["fix_rounds_completed"]),
            remediation_rounds_completed=int(data["remediation_rounds_completed"]),
            manual_gate_status=data["manual_gate_status"],
            external_check_status=data["external_check_status"],
            branch_name=data.get("branch_name"),
            worktree_path=data.get("worktree_path"),
            checkpoint_ref=data.get("checkpoint_ref"),
            terminal_state=data["terminal_state"],
            latest_paths=LatestPaths.from_dict(latest_paths),
            updated_at_utc=data["updated_at_utc"],
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["latest_paths"] = self.latest_paths.to_dict()
        return data


@dataclass
class RunState:
    schema_version: str
    run_id: str
    adapter_id: str
    repo_root: str
    playbook_source_path: str
    playbook_source_sha256: str
    normalized_plan_path: str
    base_head_sha: str
    run_branch_name: str
    created_at_utc: str
    updated_at_utc: str
    current_state: str
    current_item_id: Optional[str]
    options: RuntimeOptions
    items: list[ItemRunState]
    event_log: list[RunEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunState":
        return cls(
            schema_version=data["schema_version"],
            run_id=data["run_id"],
            adapter_id=data["adapter_id"],
            repo_root=data["repo_root"],
            playbook_source_path=data["playbook_source_path"],
            playbook_source_sha256=data["playbook_source_sha256"],
            normalized_plan_path=data["normalized_plan_path"],
            base_head_sha=data["base_head_sha"],
            run_branch_name=data["run_branch_name"],
            created_at_utc=data["created_at_utc"],
            updated_at_utc=data["updated_at_utc"],
            current_state=data["current_state"],
            current_item_id=data.get("current_item_id"),
            options=RuntimeOptions.from_dict(data["options"]),
            items=[ItemRunState.from_dict(item) for item in data.get("items", [])],
            event_log=[RunEvent.from_dict(item) for item in data.get("event_log", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "adapter_id": self.adapter_id,
            "repo_root": self.repo_root,
            "playbook_source_path": self.playbook_source_path,
            "playbook_source_sha256": self.playbook_source_sha256,
            "normalized_plan_path": self.normalized_plan_path,
            "base_head_sha": self.base_head_sha,
            "run_branch_name": self.run_branch_name,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "current_state": self.current_state,
            "current_item_id": self.current_item_id,
            "options": self.options.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "event_log": [event.to_dict() for event in self.event_log],
        }

    def get_item_state(self, item_id: str) -> ItemRunState:
        for item in self.items:
            if item.item_id == item_id:
                return item
        raise KeyError(f"Unknown item_id: {item_id}")


@dataclass
class WorktreeMetadata:
    path: str
    branch_name: str
    base_ref: str
    head_ref: str
    workspace_packet_root: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorktreeMetadata":
        return cls(
            path=data["path"],
            branch_name=data["branch_name"],
            base_ref=data["base_ref"],
            head_ref=data["head_ref"],
            workspace_packet_root=data["workspace_packet_root"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ItemContext:
    schema_version: str
    generated_at_utc: str
    run_id: str
    adapter_id: str
    item: dict[str, Any]
    worktree: dict[str, Any]
    stage_context: dict[str, Any]
    repo_scope: dict[str, Any]
    source_of_truth_paths: list[str]
    sensitive_path_globs: list[str]
    support_sections: list[dict[str, Any]]
    artifact_inputs: list[dict[str, Any]]
    verification_plan: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ItemContext":
        return cls(
            schema_version=data["schema_version"],
            generated_at_utc=data["generated_at_utc"],
            run_id=data["run_id"],
            adapter_id=data["adapter_id"],
            item=dict(data["item"]),
            worktree=dict(data["worktree"]),
            stage_context=dict(data["stage_context"]),
            repo_scope=dict(data["repo_scope"]),
            source_of_truth_paths=list(data.get("source_of_truth_paths", [])),
            sensitive_path_globs=list(data.get("sensitive_path_globs", [])),
            support_sections=list(data.get("support_sections", [])),
            artifact_inputs=list(data.get("artifact_inputs", [])),
            verification_plan=dict(data.get("verification_plan", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
