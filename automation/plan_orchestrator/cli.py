from __future__ import annotations

import argparse
import json
import sys

from .config import DEFAULT_PLAYBOOK_PATH_ENV, default_playbook_path, resolve_repo_root
from .doctor import run_doctor
from .runtime import OrchestratorError, PlanOrchestrator
from .status import list_run_statuses, load_run_status


def _print_table(rows: list[dict]) -> None:
    headers = [
        ("item_id", "STEP"),
        ("phase", "PHASE"),
        ("change_profile", "PROFILE"),
        ("requires_red_green", "R/G"),
        ("manual_gate", "MANUAL_GATE"),
        ("external_check", "EXTERNAL"),
    ]
    rendered_rows = []
    for row in rows:
        rendered_rows.append(
            {
                "item_id": row["item_id"],
                "phase": row["phase"],
                "change_profile": row["change_profile"],
                "requires_red_green": "yes" if row["requires_red_green"] else "no",
                "manual_gate": "yes" if row["manual_gate"]["required"] else "no",
                "external_check": "yes" if row["external_check"]["required"] else "no",
            }
        )

    widths = {}
    for key, label in headers:
        widths[key] = max(len(label), *(len(str(row[key])) for row in rendered_rows)) if rendered_rows else len(label)

    header_line = "  ".join(label.ljust(widths[key]) for key, label in headers)
    print(header_line)
    print("  ".join("-" * widths[key] for key, _ in headers))
    for row in rendered_rows:
        print("  ".join(str(row[key]).ljust(widths[key]) for key, _ in headers))


def _print_bullets(title: str, values: list[str]) -> None:
    print(f"{title}:")
    if not values:
        print("  - none")
        return
    for value in values:
        print(f"  - {value}")


def _print_item_text(item: dict) -> None:
    manual_gate = dict(item.get("manual_gate", {}) or {})
    external_check = dict(item.get("external_check", {}) or {})
    verification_hints = dict(item.get("verification_hints", {}) or {})

    prerequisites = list(item.get("prerequisite_item_ids", []))

    print(f"ITEM {item['item_id']}")
    print(f"Phase: {item['phase']} ({item['phase_slug']})")
    print(f"Action: {item['action']}")
    print(f"Why now: {item['why_now']}")
    print(f"Owner type: {item['owner_type']}")
    print(f"Change profile: {item['change_profile']}")
    print(f"Execution mode: {item.get('execution_mode', 'codex')}")
    print(f"Requires red/green: {'yes' if item.get('requires_red_green') else 'no'}")
    print(f"Prerequisites: {', '.join(prerequisites) if prerequisites else 'none'}")

    manual_gate_summary = "not required"
    if manual_gate.get("required"):
        manual_gate_summary = str(manual_gate.get("gate_type", "custom"))
        gate_reason = str(manual_gate.get("gate_reason", "") or "").strip()
        if gate_reason:
            manual_gate_summary += f" ({gate_reason})"
    print(f"Manual gate: {manual_gate_summary}")

    external_summary = "not required"
    if external_check.get("required"):
        external_summary = str(external_check.get("mode", "required"))
        dependencies = [
            str(value).strip()
            for value in external_check.get("dependencies", [])
            if str(value).strip()
        ]
        if dependencies:
            external_summary += f" ({', '.join(dependencies)})"
    print(f"External check: {external_summary}")
    print("")

    _print_bullets("Allowed write roots", list(item.get("allowed_write_roots", [])))
    _print_bullets("Repo surfaces", list(item.get("repo_surface_paths", [])))
    _print_bullets("Consult paths", list(item.get("consult_paths", [])))
    _print_bullets("Deliverable paths", list(item.get("deliverable_paths", [])))
    _print_bullets("Support sections", list(item.get("support_section_ids", [])))

    if manual_gate.get("required"):
        _print_bullets(
            "Manual gate evidence",
            list(manual_gate.get("required_evidence", [])),
        )
    if external_check.get("required"):
        _print_bullets(
            "External dependencies",
            list(external_check.get("dependencies", [])),
        )

    _print_bullets(
        "Required verification commands",
        list(verification_hints.get("required_commands", [])),
    )
    _print_bullets(
        "Suggested verification commands",
        list(verification_hints.get("suggested_commands", [])),
    )
    _print_bullets(
        "Required verification artifacts",
        list(verification_hints.get("required_artifacts", [])),
    )
    _print_bullets("Notes", list(item.get("notes", [])))


def _bool_label(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "no"


def _render_doctor_detail(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return _bool_label(value)
    if isinstance(value, list):
        return ", ".join(str(entry) for entry in value) if value else "none"
    return str(value)


def _print_status_table(rows: list[dict]) -> None:
    headers = [
        ("run_id", "RUN"),
        ("status_level", "STATUS"),
        ("current_state", "STATE"),
        ("current_item_id", "ITEM"),
        ("updated_at_utc", "UPDATED"),
    ]
    rendered_rows = [
        {
            "run_id": str(row.get("run_id", "")),
            "status_level": str(row.get("status_level", "")),
            "current_state": str(row.get("current_state") or ""),
            "current_item_id": str(row.get("current_item_id") or ""),
            "updated_at_utc": str(row.get("updated_at_utc") or ""),
        }
        for row in rows
    ]

    widths = {}
    for key, label in headers:
        widths[key] = max(len(label), *(len(row[key]) for row in rendered_rows)) if rendered_rows else len(label)

    print("  ".join(label.ljust(widths[key]) for key, label in headers))
    print("  ".join("-" * widths[key] for key, _ in headers))
    for row in rendered_rows:
        print("  ".join(row[key].ljust(widths[key]) for key, _ in headers))


def _print_status_text(summary: dict) -> None:
    print(f"RUN {summary['run_id']}")
    print(f"Status: {summary.get('status_level', 'unknown')} (exit {summary.get('exit_code', 0)})")
    print(f"Current state: {summary.get('current_state') or 'unknown'}")
    print(f"Current item: {summary.get('current_item_id') or 'none'}")
    if summary.get("run_branch_name"):
        print(f"Run branch: {summary['run_branch_name']}")
    if summary.get("playbook_source_path"):
        print(f"Playbook: {summary['playbook_source_path']}")
    if summary.get("normalized_plan_path"):
        print(f"Normalized plan: {summary['normalized_plan_path']}")
    if summary.get("runtime_policy_path"):
        print(f"Runtime policy: {summary['runtime_policy_path']}")
    if summary.get("runtime_policy_sha256"):
        print(f"Runtime policy sha256: {summary['runtime_policy_sha256']}")
    runtime_policy_sources = summary.get("runtime_policy_sources") or {}
    if runtime_policy_sources:
        rendered_sources = ", ".join(
            f"{key}={value}"
            for key, value in runtime_policy_sources.items()
            if value != "default"
        )
        print(f"Runtime policy sources: {rendered_sources or 'all default'}")

    pending_action = summary.get("pending_action")
    if pending_action:
        print(
            "Pending action: "
            + str(pending_action.get("kind", "unknown"))
            + " ("
            + str(pending_action.get("detail", ""))
            + ")"
        )

    current_item = summary.get("current_item")
    if current_item:
        print("")
        print(f"Item {current_item['item_id']}:")
        print(f"  state={current_item['state']}")
        print(f"  attempt={current_item['attempt_number']}")
        print(f"  terminal={current_item['terminal_state']}")
        for key, value in current_item.get("latest_paths", {}).items():
            if value:
                print(f"  {key}={value}")

    checks = summary.get("checks", {})
    if checks:
        print("")
        print("Checks:")
        for key, value in checks.items():
            print(f"  {key}={_bool_label(value)}")


def _print_doctor_text(report: dict) -> None:
    print(f"DOCTOR {report.get('repo_root', '.')}")
    print(f"Result: {'ok' if report.get('ok') else 'error'}")
    for check in report.get("checks", []):
        line = f"- {check['name']}: {check['status']}"
        if "detail" in check:
            line += f" ({check['detail']})"
        elif "checks" in check:
            rendered = ", ".join(
                f"{name}={_bool_label(value)}" for name, value in check["checks"].items()
            )
            line += f" ({rendered})"
        print(line)
        for key in (
            "missing_item_branches",
            "missing_checkpoint_refs",
            "missing_worktrees",
            "orphaned_worktrees",
            "normalized_plan_error",
            "runtime_policy_error",
        ):
            if key in check and check[key]:
                print(f"  {key}={_render_doctor_detail(check[key])}")
    repairs = report.get("repairs", [])
    if repairs:
        print("Repairs:")
        for repair in repairs:
            line = f"- {repair['name']}: {repair['status']}"
            extras = [
                f"{key}={_render_doctor_detail(value)}"
                for key, value in repair.items()
                if key not in {"name", "status"} and value is not None
            ]
            if extras:
                line += " (" + ", ".join(extras) + ")"
            print(line)


def _add_playbook_argument(parser: argparse.ArgumentParser) -> None:
    env_default = default_playbook_path()
    help_text = "Path to the markdown_playbook_v1 file."
    if env_default:
        help_text += f" Defaults to ${DEFAULT_PLAYBOOK_PATH_ENV}={env_default!r}."
        parser.add_argument("--playbook", default=env_default, help=help_text)
    else:
        parser.add_argument("--playbook", required=True, help=help_text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python automation/run_plan_orchestrator.py",
        description=(
            "Run approved markdown_playbook_v1 items one at a time in isolated git worktrees."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_items = subparsers.add_parser("list-items", help="List normalized plan items.")
    _add_playbook_argument(list_items)
    list_items.add_argument("--format", choices=("table", "json"), default="table")

    show_item = subparsers.add_parser("show-item", help="Show one normalized plan item.")
    _add_playbook_argument(show_item)
    show_item.add_argument("--item", required=True, help="Exact step_id from the playbook.")
    show_item.add_argument("--format", choices=("text", "json"), default="text")

    run_cmd = subparsers.add_parser("run", help="Run one or more items.")
    _add_playbook_argument(run_cmd)
    group = run_cmd.add_mutually_exclusive_group(required=True)
    group.add_argument("--item", help="Run one exact step_id.")
    group.add_argument("--items", help="Run a comma-separated list of exact step_id values.")
    group.add_argument("--next", action="store_true", help="Run the first unfinished item.")
    run_cmd.add_argument("--config", help="Optional JSON runtime-policy overlay.")
    run_cmd.add_argument("--external-evidence-dir")
    run_cmd.add_argument("--auto-advance", action="store_true", default=None)
    run_cmd.add_argument("--max-items", type=int)

    resume_cmd = subparsers.add_parser("resume", help="Resume an existing run.")
    resume_cmd.add_argument("--run-id", required=True)
    resume_cmd.add_argument("--external-evidence-dir")
    resume_cmd.add_argument("--auto-advance", action="store_true")

    refresh_cmd = subparsers.add_parser(
        "refresh-run",
        help="Refresh a saved run onto a descendant branch and rebuild its normalized plan.",
    )
    refresh_cmd.add_argument("--run-id", required=True)
    refresh_cmd.add_argument("--retarget-run-branch-to", required=True)

    gate_cmd = subparsers.add_parser("mark-manual-gate", help="Record a manual-gate decision.")
    gate_cmd.add_argument("--run-id", required=True)
    gate_cmd.add_argument("--item", required=True, help="Exact step_id from the run state.")
    gate_cmd.add_argument("--decision", choices=("approved", "rejected"), required=True)
    gate_cmd.add_argument("--by", required=True)
    gate_cmd.add_argument("--note", required=True)
    gate_cmd.add_argument("--evidence-path", action="append", default=[])

    status_cmd = subparsers.add_parser("status", help="Show run status and health.")
    status_group = status_cmd.add_mutually_exclusive_group(required=True)
    status_group.add_argument("--run-id", help="Show one saved run by id.")
    status_group.add_argument(
        "--all",
        action="store_true",
        help="Show every saved run under .local/automation/plan_orchestrator/runs.",
    )
    status_cmd.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render human-readable text or machine-readable JSON.",
    )
    status_cmd.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit with the reported run health code instead of always returning zero.",
    )

    doctor_cmd = subparsers.add_parser(
        "doctor",
        help="Diagnose runner state; --fix-safe may rebuild deterministic local artifacts only.",
    )
    doctor_cmd.add_argument(
        "--playbook",
        help="Optional playbook to parse and normalize without starting a run.",
    )
    doctor_cmd.add_argument(
        "--run-id",
        help="Optional saved run id to validate and inspect.",
    )
    doctor_cmd.add_argument(
        "--fix-safe",
        action="store_true",
        help="Rebuild deterministic local orchestrator artifacts only; never touches tracked repo files.",
    )
    doctor_cmd.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render human-readable text or machine-readable JSON.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = resolve_repo_root()

    try:
        if args.command == "list-items":
            orchestrator = PlanOrchestrator(repo_root)
            rows = orchestrator.list_items(args.playbook)
            if args.format == "json":
                print(json.dumps(rows, indent=2))
            else:
                _print_table(rows)
            return 0

        if args.command == "show-item":
            orchestrator = PlanOrchestrator(repo_root)
            item = orchestrator.show_item(args.playbook, args.item)
            if args.format == "json":
                print(json.dumps(item, indent=2))
            else:
                _print_item_text(item)
            return 0

        if args.command == "run":
            orchestrator = PlanOrchestrator(repo_root)
            if args.items and args.external_evidence_dir:
                raise OrchestratorError(
                    "--external-evidence-dir is allowed only with --item or resume, not with --items."
                )

            result = orchestrator.run_new(
                playbook_path=args.playbook,
                item_id=args.item,
                item_ids=[value.strip() for value in args.items.split(",")] if args.items else None,
                next_only=bool(args.next),
                external_evidence_dir=args.external_evidence_dir,
                auto_advance=args.auto_advance,
                max_items=args.max_items,
                config_path=args.config,
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "resume":
            orchestrator = PlanOrchestrator(repo_root)
            result = orchestrator.resume(
                run_id=args.run_id,
                external_evidence_dir=args.external_evidence_dir,
                auto_advance=bool(args.auto_advance),
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "refresh-run":
            orchestrator = PlanOrchestrator(repo_root)
            result = orchestrator.refresh_run(
                run_id=args.run_id,
                retarget_run_branch_to=args.retarget_run_branch_to,
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "mark-manual-gate":
            orchestrator = PlanOrchestrator(repo_root)
            result = orchestrator.mark_manual_gate(
                run_id=args.run_id,
                item_id=args.item,
                decision=args.decision,
                decided_by=args.by,
                note=args.note,
                evidence_paths=args.evidence_path,
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "status":
            if args.all:
                result = list_run_statuses(repo_root)
                if args.format == "json":
                    print(json.dumps(result, indent=2))
                else:
                    _print_status_table(result)
                return (
                    max((int(entry.get("exit_code", 0)) for entry in result), default=0)
                    if args.exit_code
                    else 0
                )

            result = load_run_status(repo_root, args.run_id)
            if args.format == "json":
                print(json.dumps(result, indent=2))
            else:
                _print_status_text(result)
            return int(result.get("exit_code", 0)) if args.exit_code else 0

        if args.command == "doctor":
            result = run_doctor(
                repo_root,
                playbook_path=args.playbook,
                run_id=args.run_id,
                fix_safe=bool(args.fix_safe),
            )
            if args.format == "json":
                print(json.dumps(result, indent=2))
            else:
                _print_doctor_text(result)
            return int(result.get("exit_code", 0 if result.get("ok", True) else 1))

        raise OrchestratorError(f"Unknown command: {args.command}")
    except (OrchestratorError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
