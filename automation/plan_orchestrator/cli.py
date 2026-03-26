from __future__ import annotations

import argparse
import json
import sys

from .config import DEFAULT_PLAYBOOK_PATH_ENV, default_playbook_path, resolve_repo_root
from .runtime import OrchestratorError, PlanOrchestrator


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
    run_cmd.add_argument("--external-evidence-dir")
    run_cmd.add_argument("--auto-advance", action="store_true")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = resolve_repo_root()
    orchestrator = PlanOrchestrator(repo_root)

    try:
        if args.command == "list-items":
            rows = orchestrator.list_items(args.playbook)
            if args.format == "json":
                print(json.dumps(rows, indent=2))
            else:
                _print_table(rows)
            return 0

        if args.command == "show-item":
            item = orchestrator.show_item(args.playbook, args.item)
            if args.format == "json":
                print(json.dumps(item, indent=2))
            else:
                _print_item_text(item)
            return 0

        if args.command == "run":
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
                auto_advance=bool(args.auto_advance),
                max_items=args.max_items,
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "resume":
            result = orchestrator.resume(
                run_id=args.run_id,
                external_evidence_dir=args.external_evidence_dir,
                auto_advance=bool(args.auto_advance),
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "refresh-run":
            result = orchestrator.refresh_run(
                run_id=args.run_id,
                retarget_run_branch_to=args.retarget_run_branch_to,
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "mark-manual-gate":
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

        raise OrchestratorError(f"Unknown command: {args.command}")
    except (OrchestratorError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
