from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .validators import dedupe_preserve_order, ensure_directory, write_json_atomic

MUTATION_REPORT_SOURCE_TYPE = "mutation_report"
MUTATION_CONTROL_BLOCKED_EXTERNAL = "mutation_control_blocked_external"
MUTATION_CONTROL_NEEDS_HUMAN_INPUT = "mutation_control_needs_human_input"
MUTATION_CONTROL_ESCALATE = "mutation_control_escalate"
MUTATION_CONTROL_UNRESOLVED_ITEMS = "mutation_control_unresolved_items"


def canonical_finding_id(
    *,
    title: str,
    category: str,
    file_paths: list[str],
    evidence: list[str],
) -> str:
    payload = json.dumps(
        {
            "title": title.strip(),
            "category": category.strip(),
            "file_paths": sorted(set(file_paths)),
            "evidence": sorted(set(evidence)),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"finding_{digest}"


def _merge_audit_finding(finding: dict[str, Any], source_type: str) -> dict[str, Any]:
    canonical_id = canonical_finding_id(
        title=finding["title"],
        category=finding["category"],
        file_paths=list(finding.get("file_paths", [])),
        evidence=list(finding.get("evidence", [])),
    )
    recommended_owner = "codex_fix"
    disposition = "actionable"

    if finding["category"] in {"manual_gate"}:
        recommended_owner = "human"
        disposition = "requires_human_judgment"
    if finding["category"] in {"external_dependency"}:
        recommended_owner = "operator_external"
        disposition = "blocked_external"

    return {
        "canonical_id": canonical_id,
        "title": finding["title"],
        "severity": finding["severity"],
        "category": finding["category"],
        "disposition": disposition,
        "recommended_owner": recommended_owner,
        "source_refs": [
            {
                "source_type": source_type,
                "source_id": finding["finding_id"],
            }
        ],
        "file_paths": list(finding.get("file_paths", [])),
        "evidence": list(finding.get("evidence", [])),
        "recommended_action": finding["recommended_action"],
        "acceptance_check": finding["why_it_matters"],
        "is_blocking": bool(finding["is_blocking"]),
    }


def _mutation_report_unresolved_items(mutation_report: dict[str, Any] | None) -> list[str]:
    if not mutation_report:
        return []

    raw_items: list[str] = []
    for key in ("unresolved_dependencies", "residual_open_items"):
        values = mutation_report.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if stripped:
                raw_items.append(stripped)
    return dedupe_preserve_order(raw_items)


def _mutation_report_control(mutation_report: dict[str, Any] | None) -> dict[str, Any]:
    if not mutation_report:
        return {
            "stage": None,
            "verdict": None,
            "next_recommended_state": None,
            "summary": "",
            "signals": [],
            "signal_refs": [],
            "unresolved_items": [],
        }

    verdict = str(mutation_report.get("verdict", "") or "").strip()
    next_state = str(mutation_report.get("next_recommended_state", "") or "").strip()
    unresolved_items = _mutation_report_unresolved_items(mutation_report)

    signal_refs: list[dict[str, str]] = []
    if verdict == "blocked_external" or next_state == "blocked_external":
        signal_refs.append(
            {"signal": "blocked_external", "source_id": MUTATION_CONTROL_BLOCKED_EXTERNAL}
        )
    if verdict in {"needs_human_input"} or next_state == "awaiting_human_gate":
        signal_refs.append(
            {"signal": "needs_human_input", "source_id": MUTATION_CONTROL_NEEDS_HUMAN_INPUT}
        )
    if verdict == "blocked" or next_state == "escalate":
        signal_refs.append({"signal": "escalate", "source_id": MUTATION_CONTROL_ESCALATE})
    if unresolved_items:
        signal_refs.append(
            {"signal": "unresolved_items", "source_id": MUTATION_CONTROL_UNRESOLVED_ITEMS}
        )

    return {
        "stage": mutation_report.get("stage"),
        "verdict": verdict or None,
        "next_recommended_state": next_state or None,
        "summary": str(mutation_report.get("summary", "") or ""),
        "signals": [entry["signal"] for entry in signal_refs],
        "signal_refs": signal_refs,
        "unresolved_items": unresolved_items,
    }


def _mutation_control_finding(
    *,
    mutation_report: dict[str, Any],
    source_id: str,
    title: str,
    severity: str,
    category: str,
    disposition: str,
    recommended_owner: str,
    recommended_action: str,
    acceptance_check: str,
    extra_evidence: list[str] | None = None,
) -> dict[str, Any]:
    evidence: list[str] = []
    summary = str(mutation_report.get("summary", "") or "").strip()
    verdict = str(mutation_report.get("verdict", "") or "").strip()
    next_state = str(mutation_report.get("next_recommended_state", "") or "").strip()
    stage = str(mutation_report.get("stage", "") or "").strip()
    if summary:
        evidence.append(summary)
    if stage:
        evidence.append(f"stage={stage}")
    if verdict:
        evidence.append(f"verdict={verdict}")
    if next_state:
        evidence.append(f"next_recommended_state={next_state}")
    for item in extra_evidence or []:
        if item:
            evidence.append(item)

    file_paths = [
        path
        for path in mutation_report.get("files_touched", [])
        if isinstance(path, str) and path
    ]
    evidence = dedupe_preserve_order(evidence)
    file_paths = dedupe_preserve_order(file_paths)

    return {
        "canonical_id": canonical_finding_id(
            title=title,
            category=category,
            file_paths=file_paths,
            evidence=evidence,
        ),
        "title": title,
        "severity": severity,
        "category": category,
        "disposition": disposition,
        "recommended_owner": recommended_owner,
        "source_refs": [
            {
                "source_type": MUTATION_REPORT_SOURCE_TYPE,
                "source_id": source_id,
            }
        ],
        "file_paths": file_paths,
        "evidence": evidence,
        "recommended_action": recommended_action,
        "acceptance_check": acceptance_check,
        "is_blocking": True,
    }


def _mutation_report_findings(mutation_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not mutation_report:
        return []

    control = _mutation_report_control(mutation_report)
    findings: list[dict[str, Any]] = []
    unresolved_items = control["unresolved_items"]
    residual_risks = [
        risk
        for risk in mutation_report.get("residual_risks", [])
        if isinstance(risk, str) and risk
    ]

    if any(entry["signal"] == "blocked_external" for entry in control["signal_refs"]):
        findings.append(
            _mutation_control_finding(
                mutation_report=mutation_report,
                source_id=MUTATION_CONTROL_BLOCKED_EXTERNAL,
                title="Mutation stage reported an unresolved external dependency",
                severity="high",
                category="external_dependency",
                disposition="blocked_external",
                recommended_owner="operator_external",
                recommended_action="Provide the missing external dependency before resuming automation.",
                acceptance_check="The mutation report no longer requests blocked_external and the missing external dependency is available.",
                extra_evidence=unresolved_items + residual_risks,
            )
        )

    if any(entry["signal"] == "needs_human_input" for entry in control["signal_refs"]):
        findings.append(
            _mutation_control_finding(
                mutation_report=mutation_report,
                source_id=MUTATION_CONTROL_NEEDS_HUMAN_INPUT,
                title="Mutation stage requested human input before completion",
                severity="high",
                category="manual_gate",
                disposition="requires_human_judgment",
                recommended_owner="human",
                recommended_action="Collect the required human decision or approval before passing the item.",
                acceptance_check="The mutation report no longer requests human input and the required human decision is recorded.",
                extra_evidence=unresolved_items + residual_risks,
            )
        )

    if any(entry["signal"] == "escalate" for entry in control["signal_refs"]):
        findings.append(
            _mutation_control_finding(
                mutation_report=mutation_report,
                source_id=MUTATION_CONTROL_ESCALATE,
                title="Mutation stage requested escalation",
                severity="high",
                category="process",
                disposition="actionable",
                recommended_owner="none",
                recommended_action="Escalate instead of continuing automatic mutation until the blocker is resolved.",
                acceptance_check="The mutation report no longer requests escalation and the blocker is resolved or explicitly suppressed.",
                extra_evidence=unresolved_items + residual_risks,
            )
        )

    if any(entry["signal"] == "unresolved_items" for entry in control["signal_refs"]):
        findings.append(
            _mutation_control_finding(
                mutation_report=mutation_report,
                source_id=MUTATION_CONTROL_UNRESOLVED_ITEMS,
                title="Mutation stage left unresolved dependencies or open items",
                severity="high",
                category="process",
                disposition="actionable",
                recommended_owner="none",
                recommended_action="Resolve or explicitly suppress the unresolved dependency/open-item list before passing the item.",
                acceptance_check="The mutation report has no unresolved_dependencies/residual_open_items, or triage suppresses them explicitly.",
                extra_evidence=unresolved_items,
            )
        )

    return findings


def _verification_findings(verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for result in verification_report.get("command_results", []):
        if result["status"] != "fail":
            continue
        if not result.get("required", False):
            continue
        title = f"Verification command failed: {result['label']}"
        evidence = [result["command"]]
        if result.get("log_path"):
            evidence.append(result["log_path"])
        findings.append(
            {
                "canonical_id": canonical_finding_id(
                    title=title,
                    category="correctness",
                    file_paths=[],
                    evidence=evidence,
                ),
                "title": title,
                "severity": "high",
                "category": "correctness",
                "disposition": "actionable",
                "recommended_owner": "codex_fix",
                "source_refs": [
                    {
                        "source_type": "verification",
                        "source_id": f"verification_command_{result['label'].replace('#', '_')}",
                    }
                ],
                "evidence": evidence,
                "recommended_action": "Make the verification command pass in the worktree verification gate.",
                "acceptance_check": f"`{result['command']}` exits 0 during verification.",
                "is_blocking": True,
            }
        )

    for result in verification_report.get("artifact_checks", []):
        if result["status"] != "fail":
            continue
        title = f"Required artifact check failed: {result['path']}"
        evidence = [result["reason"], result["path"]]
        findings.append(
            {
                "canonical_id": canonical_finding_id(
                    title=title,
                    category="artifact",
                    file_paths=[result["path"]],
                    evidence=evidence,
                ),
                "title": title,
                "severity": "medium",
                "category": "artifact",
                "disposition": "actionable",
                "recommended_owner": "codex_fix",
                "source_refs": [
                    {
                        "source_type": "verification",
                        "source_id": f"verification_artifact_{result['path'].replace('/', '_')}",
                    }
                ],
                "evidence": evidence,
                "recommended_action": "Create or correct the required artifact so the verification check passes.",
                "acceptance_check": result["reason"],
                "is_blocking": True,
            }
        )

    scope = verification_report.get("scope_check", {})
    if scope.get("status") == "fail":
        evidence = list(scope.get("out_of_scope_paths", []))
        findings.append(
            {
                "canonical_id": canonical_finding_id(
                    title="Scope violation detected by verification",
                    category="scope",
                    file_paths=evidence,
                    evidence=evidence + [scope.get("note", "")],
                ),
                "title": "Scope violation detected by verification",
                "severity": "high",
                "category": "scope",
                "disposition": "actionable",
                "recommended_owner": "none",
                "source_refs": [
                    {
                        "source_type": "verification",
                        "source_id": "verification_scope_check",
                    }
                ],
                "evidence": evidence + [scope.get("note", "")],
                "recommended_action": "Escalate or restart with corrected scope; do not continue automatic mutation from this state.",
                "acceptance_check": "All committed changes stay within allowed write roots.",
                "is_blocking": True,
            }
        )

    return findings


def merge_findings(
    *,
    mutation_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any],
    codex_audit_report: dict[str, Any],
    claude_audit_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged: dict[str, dict[str, Any]] = {}
    suppressed: list[dict[str, Any]] = []

    candidates = []
    for finding in codex_audit_report.get("findings", []):
        candidates.append((_merge_audit_finding(finding, "codex_audit"), "codex_audit", finding["finding_id"]))
    for finding in claude_audit_report.get("findings", []):
        candidates.append((_merge_audit_finding(finding, "claude_audit"), "claude_audit", finding["finding_id"]))
    for finding in _mutation_report_findings(mutation_report):
        source_ref = finding["source_refs"][0]
        candidates.append((finding, source_ref["source_type"], source_ref["source_id"]))
    for finding in _verification_findings(verification_report):
        source_ref = finding["source_refs"][0]
        candidates.append((finding, source_ref["source_type"], source_ref["source_id"]))

    for normalized, source_type, source_id in candidates:
        key = normalized["canonical_id"]
        if key not in merged:
            merged[key] = normalized
            continue

        existing = merged[key]
        existing["source_refs"] = existing["source_refs"] + normalized["source_refs"]
        existing["file_paths"] = dedupe_preserve_order(existing["file_paths"] + normalized["file_paths"])
        existing["evidence"] = dedupe_preserve_order(existing["evidence"] + normalized["evidence"])
        existing["is_blocking"] = bool(existing["is_blocking"] or normalized["is_blocking"])
        suppressed.append(
            {
                "source_type": source_type,
                "source_id": source_id,
                "suppression_reason": f"duplicate_of:{key}",
            }
        )

    merged_findings = sorted(
        merged.values(),
        key=lambda value: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[value["severity"]],
            value["canonical_id"],
        ),
    )
    return merged_findings, suppressed


def write_merged_findings_packet(
    *,
    output_path: Path,
    item_id: str,
    attempt_number: int,
    mutation_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any],
    codex_audit_report: dict[str, Any],
    claude_audit_report: dict[str, Any],
) -> dict[str, Any]:
    ensure_directory(output_path.parent)
    merged_findings, suppressed = merge_findings(
        mutation_report=mutation_report,
        verification_report=verification_report,
        codex_audit_report=codex_audit_report,
        claude_audit_report=claude_audit_report,
    )
    packet = {
        "item_id": item_id,
        "attempt_number": attempt_number,
        "mutation_report_control": _mutation_report_control(mutation_report),
        "verification_summary": verification_report.get("summary", ""),
        "codex_audit_summary": codex_audit_report.get("summary", ""),
        "claude_audit_summary": claude_audit_report.get("summary", ""),
        "merged_findings": merged_findings,
        "suppressed_findings": suppressed,
    }
    write_json_atomic(output_path, packet)
    return packet
