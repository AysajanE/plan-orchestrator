from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BasePlanAdapter
from ..models import NormalizedPlan, PlanItem, SupportSection
from ..playbook_parser import extract_repo_paths_from_text
from ..validators import dedupe_preserve_order, slugify, utc_now_iso, validate_named_schema


class MarkdownPlaybookAdapter(BasePlanAdapter):
    adapter_id = "markdown_playbook_v1"

    REQUIRED_COLUMNS = {
        "step_id",
        "phase",
        "action",
        "why_now",
        "owner_type",
        "prerequisites",
        "repo_surfaces",
        "deliverable",
        "exit_criteria",
        "allowed_write_roots",
        "requires_red_green",
    }
    RESERVED_COLUMNS = ("change_profile", "execution_mode", "host_commands")
    TRUE_VALUES = {"1", "true", "yes", "y"}
    FALSE_VALUES = {"0", "false", "no", "n"}
    MANUAL_GATE_TYPES = {
        "none",
        "signoff",
        "approval",
        "operator_confirmation",
        "security_review",
        "presenter_review",
        "custom",
    }
    EXTERNAL_CHECK_MODES = {"none", "human_supplied_evidence_required"}
    FORBIDDEN_WRITE_ROOTS = (
        ".local",
        ".git",
        ".codex",
        ".claude",
        ".mcp.json",
        "ops/config",
        "secrets",
    )

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def normalize(self, parsed: dict[str, Any], playbook_path: str | Path) -> NormalizedPlan:
        rows = list(parsed.get("ordered_execution_rows", []))
        self._validate_ordered_execution_rows(rows)

        all_phase_slugs = [
            slugify(self._require_text(row.get("phase", ""), "phase", str(row.get("step_id", ""))))
            for row in rows
        ]
        support_sections = self._build_support_sections(parsed, all_phase_slugs)
        support_by_id = {section.section_id: section for section in support_sections}

        items: list[PlanItem] = []
        for order, row in enumerate(rows, start=1):
            items.append(
                self._row_to_item(
                    row=row,
                    order=order,
                    support_sections=support_by_id,
                )
            )

        global_support_section_ids = [
            section.section_id
            for section in support_sections
            if section.section_kind in {"global_context", "shared_guidance", "risk_register"}
        ]
        primary_goal = (
            self._first_non_empty_line(self._section_body(parsed, "1"))
            or "Execute the approved playbook one item at a time through the plan orchestrator."
        )
        immediate_target = (
            f"First unfinished item: {items[0].item_id}" if items else "First unfinished item."
        )

        plan = NormalizedPlan(
            schema_version="plan_orchestrator.normalized_plan.v1",
            adapter_id=self.adapter_id,
            plan_source={
                "path": Path(playbook_path).as_posix(),
                "source_kind": "markdown_playbook_v1",
                "sha256": parsed["sha256"],
                "title": Path(playbook_path).name,
            },
            generated_at_utc=utc_now_iso(),
            global_context={
                "primary_goal": primary_goal,
                "immediate_target": immediate_target,
                "default_runtime_profile": {
                    "offline_default": True,
                    "auto_advance_default": False,
                    "max_fix_rounds_default": 2,
                    "max_remediation_rounds_default": 1,
                },
                "global_support_section_ids": global_support_section_ids,
                "notes": [
                    "Immediate item source is the approved markdown playbook itself.",
                    "The runtime snapshots the source markdown and normalizes it before execution.",
                    "allowed_write_roots and requires_red_green are authored in the playbook.",
                    "change_profile is derived during normalization.",
                    "execution_mode is fixed to codex in canonical public v1.",
                    "host_commands remain reserved for a future extension path and are held empty in canonical public v1.",
                ],
            },
            support_sections=support_sections,
            items=items,
        )
        validate_named_schema("normalized_plan.schema.json", plan.to_dict())
        return plan

    def _validate_ordered_execution_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            raise ValueError("Ordered Execution Plan table contains no rows.")

        columns = {key for key in rows[0].keys() if key != "source_row"}
        missing = sorted(self.REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(
                "Ordered Execution Plan table is missing required columns: " + ", ".join(missing)
            )

    def _row_to_item(
        self,
        *,
        row: dict[str, Any],
        order: int,
        support_sections: dict[str, SupportSection],
    ) -> PlanItem:
        item_id = self._require_text(row.get("step_id", ""), "step_id", "<row>")
        self._assert_no_reserved_overrides(row, item_id)

        phase = self._require_text(row.get("phase", ""), "phase", item_id)
        phase_slug = slugify(phase)
        action = self._require_text(row.get("action", ""), "action", item_id)
        why_now = self._require_text(row.get("why_now", ""), "why_now", item_id)
        owner_type = self._require_text(row.get("owner_type", ""), "owner_type", item_id)
        prerequisites_raw = str(row.get("prerequisites", "") or "").strip()
        repo_surfaces_cell = self._require_text(row.get("repo_surfaces", ""), "repo_surfaces", item_id)
        deliverable = self._require_text(row.get("deliverable", ""), "deliverable", item_id)
        exit_criteria = self._require_text(row.get("exit_criteria", ""), "exit_criteria", item_id)

        repo_surfaces_raw = self._split_semicolon_cell(repo_surfaces_cell)
        repo_surface_paths = dedupe_preserve_order(extract_repo_paths_from_text(repo_surfaces_cell))
        if not repo_surface_paths:
            raise ValueError(
                f"Item {item_id}: `repo_surfaces` must contain at least one concrete repo-relative path."
            )

        deliverable_paths = dedupe_preserve_order(extract_repo_paths_from_text(deliverable))
        if not deliverable_paths:
            raise ValueError(
                f"Item {item_id}: `deliverable` must contain at least one concrete repo-relative path."
            )

        allowed_write_roots = self._parse_allowed_write_roots(
            row.get("allowed_write_roots", ""),
            item_id=item_id,
        )
        requires_red_green = self._parse_bool_cell(
            row.get("requires_red_green", ""),
            field_name="requires_red_green",
            item_id=item_id,
        )
        change_profile = self._derive_change_profile(
            requires_red_green=requires_red_green,
            deliverable_paths=deliverable_paths,
            allowed_write_roots=allowed_write_roots,
        )
        manual_gate = self._parse_manual_gate(row=row, item_id=item_id, exit_criteria=exit_criteria)
        external_dependencies_raw = self._split_semicolon_cell(
            str(row.get("external_dependencies", "") or "")
        )
        external_check = self._parse_external_check(
            row=row,
            item_id=item_id,
            external_dependencies_raw=external_dependencies_raw,
        )
        consult_paths = dedupe_preserve_order(
            repo_surface_paths
            + extract_repo_paths_from_text(str(row.get("consult_paths", "") or ""))
        )
        verification_hints = self._parse_verification_hints(
            row=row,
            item_id=item_id,
            deliverable_paths=deliverable_paths,
            requires_red_green=requires_red_green,
        )
        support_section_ids = self._support_sections_for_phase(
            phase_slug=phase_slug,
            support_sections=support_sections,
        )

        notes = dedupe_preserve_order(
            self._split_semicolon_cell(str(row.get("notes", "") or ""))
            + [
                "Immediate source is the frozen markdown playbook snapshot.",
                (
                    "Behavioral changes require real Red/Green evidence."
                    if requires_red_green
                    else "Non-behavioral items may verify through artifact and content checks without fabricated failing tests."
                ),
            ]
            + (
                ["Manual review is required before the item can be treated as finally passed."]
                if manual_gate["required"]
                else []
            )
            + (
                ["This item requires human-supplied external evidence; the runtime must not browse the web to satisfy it."]
                if external_check["required"]
                else []
            )
        )

        return PlanItem(
            item_id=item_id,
            order=order,
            phase=phase,
            phase_slug=phase_slug,
            action=action,
            why_now=why_now,
            owner_type=owner_type,
            prerequisites_raw=prerequisites_raw,
            prerequisite_item_ids=self._parse_prerequisites(prerequisites_raw),
            repo_surfaces_raw=repo_surfaces_raw,
            repo_surface_paths=repo_surface_paths,
            external_dependencies_raw=external_dependencies_raw,
            deliverable=deliverable,
            deliverable_paths=deliverable_paths,
            exit_criteria=exit_criteria,
            change_profile=change_profile,
            execution_mode="codex",
            host_commands=[],
            requires_red_green=requires_red_green,
            manual_gate=manual_gate,
            external_check=external_check,
            verification_hints=verification_hints,
            source_row=dict(row["source_row"]),
            support_section_ids=support_section_ids,
            consult_paths=consult_paths,
            allowed_write_roots=allowed_write_roots,
            notes=notes,
        )

    def _assert_no_reserved_overrides(self, row: dict[str, Any], item_id: str) -> None:
        offending = [
            column
            for column in self.RESERVED_COLUMNS
            if str(row.get(column, "") or "").strip()
        ]
        if offending:
            raise ValueError(
                f"Item {item_id}: reserved authored columns are not allowed in markdown_playbook_v1: "
                + ", ".join(offending)
                + ". Keep them empty and let normalization derive the internal fields."
            )

    def _parse_manual_gate(
        self,
        *,
        row: dict[str, Any],
        item_id: str,
        exit_criteria: str,
    ) -> dict[str, Any]:
        gate_type = str(row.get("manual_gate", "") or "").strip().lower() or "none"
        if gate_type not in self.MANUAL_GATE_TYPES:
            raise ValueError(
                f"Item {item_id}: `manual_gate` must be one of {sorted(self.MANUAL_GATE_TYPES)}."
            )
        if gate_type == "none":
            return {
                "required": False,
                "gate_type": "none",
                "gate_reason": "",
                "required_evidence": [],
            }

        gate_reason = str(row.get("manual_gate_reason", "") or "").strip() or exit_criteria
        evidence = self._split_semicolon_cell(str(row.get("manual_gate_evidence", "") or "")) or [
            "decision record"
        ]
        return {
            "required": True,
            "gate_type": gate_type,
            "gate_reason": gate_reason,
            "required_evidence": evidence,
        }

    def _parse_external_check(
        self,
        *,
        row: dict[str, Any],
        item_id: str,
        external_dependencies_raw: list[str],
    ) -> dict[str, Any]:
        mode = str(row.get("external_check", "") or "").strip().lower() or "none"
        if mode not in self.EXTERNAL_CHECK_MODES:
            raise ValueError(
                f"Item {item_id}: `external_check` must be one of {sorted(self.EXTERNAL_CHECK_MODES)}."
            )
        return {
            "required": mode != "none",
            "mode": mode,
            "dependencies": external_dependencies_raw if mode != "none" else [],
        }

    def _parse_verification_hints(
        self,
        *,
        row: dict[str, Any],
        item_id: str,
        deliverable_paths: list[str],
        requires_red_green: bool,
    ) -> dict[str, Any]:
        required_commands = self._parse_command_cell(
            str(row.get("required_verification_commands", "") or "")
        )
        suggested_commands = self._parse_command_cell(
            str(row.get("suggested_verification_commands", "") or "")
        )
        required_artifacts = dedupe_preserve_order(
            extract_repo_paths_from_text(str(row.get("required_verification_artifacts", "") or ""))
            or deliverable_paths
        )

        if requires_red_green and not required_commands:
            raise ValueError(
                f"Item {item_id}: requires_red_green=true requires at least one required_verification_commands entry."
            )
        if not required_artifacts and not required_commands and not suggested_commands:
            raise ValueError(
                f"Item {item_id}: at least one verification artifact or verification command is required."
            )

        return {
            "required_artifacts": required_artifacts,
            "required_commands": required_commands,
            "suggested_commands": suggested_commands,
        }

    def _derive_change_profile(
        self,
        *,
        requires_red_green: bool,
        deliverable_paths: list[str],
        allowed_write_roots: list[str],
    ) -> str:
        if requires_red_green:
            return "behavioral_code"

        docs_like_deliverables = all(
            path.startswith("docs/") or path.endswith(".md") or path.endswith(".json")
            for path in deliverable_paths
        )
        docs_like_roots = all(root.startswith("docs") for root in allowed_write_roots)
        if docs_like_deliverables and docs_like_roots:
            return "docs_only"
        return "mixed"

    def _parse_prerequisites(self, raw: str) -> list[str]:
        if not raw or raw.lower() == "none":
            return []

        out: list[str] = []
        for token in [part.strip() for part in raw.split(",") if part.strip()]:
            if "-" in token:
                left, right = [value.strip() for value in token.split("-", 1)]
                if left.isdigit() and right.isdigit():
                    width = max(len(left), len(right))
                    for value in range(int(left), int(right) + 1):
                        out.append(str(value).zfill(width))
                    continue
            out.append(token)
        return dedupe_preserve_order(out)

    def _parse_allowed_write_roots(self, raw_value: Any, *, item_id: str) -> list[str]:
        roots = [value.strip().strip("/") for value in str(raw_value or "").split(";") if value.strip()]
        if not roots:
            raise ValueError(f"Item {item_id}: `allowed_write_roots` must contain at least one repo-relative root.")

        for root in roots:
            if root.startswith("/"):
                raise ValueError(f"Item {item_id}: `allowed_write_roots` must be repo-relative: {root}")
            if any(root == forbidden or root.startswith(f"{forbidden}/") for forbidden in self.FORBIDDEN_WRITE_ROOTS):
                raise ValueError(
                    f"Item {item_id}: `allowed_write_roots` may not point at forbidden runtime roots: {root}"
                )
        return dedupe_preserve_order(roots)

    def _parse_bool_cell(self, raw_value: Any, *, field_name: str, item_id: str) -> bool:
        value = str(raw_value or "").strip().lower()
        if value in self.TRUE_VALUES:
            return True
        if value in self.FALSE_VALUES:
            return False
        raise ValueError(
            f"Item {item_id}: `{field_name}` must be one of {sorted(self.TRUE_VALUES | self.FALSE_VALUES)}."
        )

    def _split_semicolon_cell(self, value: str) -> list[str]:
        raw = [part.strip() for part in value.split(";")]
        return [part for part in raw if part and part.lower() != "none"]

    def _parse_command_cell(self, value: str) -> list[str]:
        commands: list[str] = []
        for part in self._split_semicolon_cell(value):
            stripped = part.strip()
            if stripped.startswith("`") and stripped.endswith("`") and stripped.count("`") == 2:
                stripped = stripped[1:-1].strip()
            commands.append(stripped)
        return commands

    def _require_text(self, raw_value: Any, field_name: str, item_id: str) -> str:
        value = str(raw_value or "").strip()
        if not value:
            raise ValueError(f"Item {item_id}: `{field_name}` must be non-empty.")
        return value

    def _build_support_sections(
        self,
        parsed: dict[str, Any],
        all_phase_slugs: list[str],
    ) -> list[SupportSection]:
        sections: list[SupportSection] = []

        section_1 = self._section(parsed, "1")
        if section_1 is not None and section_1["body_markdown"].strip():
            sections.append(
                SupportSection(
                    section_id="sec1_plan-context",
                    title=section_1["title"],
                    section_kind="global_context",
                    body_markdown=section_1["body_markdown"].strip(),
                    applies_to_phase_slugs=all_phase_slugs,
                )
            )

        section_3 = self._section(parsed, "3")
        if section_3 is not None:
            for subsection in section_3.get("subsections", []):
                body = subsection["body_markdown"].strip()
                if not body:
                    continue
                sections.append(
                    SupportSection(
                        section_id=f"sec3_{subsection['slug']}",
                        title=subsection["title"],
                        section_kind="phase_detail",
                        body_markdown=body,
                        applies_to_phase_slugs=[slugify(subsection["title"])],
                    )
                )

        section_4 = self._section(parsed, "4")
        if section_4 is not None:
            if section_4.get("subsections"):
                for subsection in section_4["subsections"]:
                    body = subsection["body_markdown"].strip()
                    if not body:
                        continue
                    sections.append(
                        SupportSection(
                            section_id=f"sec4_{subsection['slug']}",
                            title=subsection["title"],
                            section_kind="shared_guidance",
                            body_markdown=body,
                            applies_to_phase_slugs=all_phase_slugs,
                        )
                    )
            elif section_4["body_markdown"].strip():
                sections.append(
                    SupportSection(
                        section_id=f"sec4_{section_4['slug']}",
                        title=section_4["title"],
                        section_kind="shared_guidance",
                        body_markdown=section_4["body_markdown"].strip(),
                        applies_to_phase_slugs=all_phase_slugs,
                    )
                )

        section_5 = self._section(parsed, "5")
        if section_5 is not None and section_5["body_markdown"].strip():
            sections.append(
                SupportSection(
                    section_id=f"sec5_{section_5['slug']}",
                    title=section_5["title"],
                    section_kind="risk_register",
                    body_markdown=section_5["body_markdown"].strip(),
                    applies_to_phase_slugs=all_phase_slugs,
                )
            )

        section_6 = self._section(parsed, "6")
        if section_6 is not None and section_6["body_markdown"].strip():
            sections.append(
                SupportSection(
                    section_id=f"sec6_{section_6['slug']}",
                    title=section_6["title"],
                    section_kind="informational",
                    body_markdown=section_6["body_markdown"].strip(),
                    applies_to_phase_slugs=[],
                )
            )

        return sections

    def _support_sections_for_phase(
        self,
        *,
        phase_slug: str,
        support_sections: dict[str, SupportSection],
    ) -> list[str]:
        section_ids: list[str] = []
        for section in support_sections.values():
            if section.section_kind == "informational":
                continue
            if not section.applies_to_phase_slugs or phase_slug in section.applies_to_phase_slugs:
                section_ids.append(section.section_id)
        return dedupe_preserve_order(section_ids)

    def _section(self, parsed: dict[str, Any], number: str) -> dict[str, Any] | None:
        for section in parsed.get("sections", []):
            if section.get("number") == number:
                return section
        return None

    def _section_body(self, parsed: dict[str, Any], number: str) -> str:
        section = self._section(parsed, number)
        if section is None:
            return ""
        return str(section.get("body_markdown", "") or "")

    def _first_non_empty_line(self, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""
