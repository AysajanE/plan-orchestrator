from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .validators import ValidationError, ensure_directory, load_json, validate_json_file, validate_named_schema, write_json_atomic


class StageProcessError(RuntimeError):
    pass


@dataclass
class StageResult:
    report: dict[str, Any]
    report_path: Path
    stdout_log: Path | None
    stderr_log: Path
    effort_used: str | None = None


_MARKDOWN_JSON_BLOCK_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_MARKDOWN_JSON_BLOCK_SEARCH_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise StageProcessError(f"Required command is not installed or not in PATH: {command}")


def _run(
    *,
    argv: list[str],
    cwd: Path,
    timeout_sec: int,
    stdin_text: str | None,
    stdout_path: Path | None,
    stderr_path: Path,
) -> int:
    ensure_directory(stderr_path.parent)
    if stdout_path is not None:
        ensure_directory(stdout_path.parent)

    stdout_handle = stdout_path.open("w", encoding="utf-8") if stdout_path else subprocess.DEVNULL
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            input=stdin_text,
            text=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
            timeout=timeout_sec,
            check=False,
        )
        return completed.returncode
    finally:
        if stdout_path is not None:
            stdout_handle.close()
        stderr_handle.close()


def run_codex_stage(
    *,
    worktree_path: Path,
    prompt_path: Path,
    schema_path: Path,
    report_path: Path,
    stdout_log: Path,
    stderr_log: Path,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    timeout_sec: int,
) -> StageResult:
    _require_command("codex")
    prompt_text = prompt_path.read_text(encoding="utf-8")

    argv = [
        "codex",
        "exec",
        "-C",
        str(worktree_path),
        "-m",
        model,
        "-s",
        sandbox,
        "--output-schema",
        str(schema_path),
        "-o",
        str(report_path),
        "--ephemeral",
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "-c",
        "web_search=disabled",
        "-",
    ]
    return_code = _run(
        argv=argv,
        cwd=worktree_path,
        timeout_sec=timeout_sec,
        stdin_text=prompt_text,
        stdout_path=stdout_log,
        stderr_path=stderr_log,
    )
    if return_code != 0:
        raise StageProcessError(
            f"codex exec failed with exit code {return_code}. See {stderr_log.as_posix()}"
        )

    report = validate_json_file(schema_path, report_path)
    return StageResult(
        report=report,
        report_path=report_path,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
    )


def _looks_like_unsupported_claude_max(stderr_text: str) -> bool:
    lowered = stderr_text.lower()
    return "max" in lowered and (
        "unsupported" in lowered
        or "opus 4.6 only" in lowered
        or "invalid value" in lowered
        or "not available" in lowered
    )


def _run_claude_once(
    *,
    worktree_path: Path,
    prompt_path: Path,
    schema_json: str,
    report_path: Path,
    stderr_log: Path,
    model: str,
    effort: str,
    max_turns: int,
    timeout_sec: int,
) -> int:
    argv = [
        "claude",
        "-p",
        "Review item using the appended audit instructions and the workspace packet files. Return only schema-valid JSON.",
        "--model",
        model,
        "--effort",
        effort,
        "--no-session-persistence",
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        "plan",
        "--tools",
        "Read,Glob,Grep",
        "--append-system-prompt-file",
        str(prompt_path),
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
    ]
    return _run(
        argv=argv,
        cwd=worktree_path,
        timeout_sec=timeout_sec,
        stdin_text=None,
        stdout_path=report_path,
        stderr_path=stderr_log,
    )


def _parse_embedded_json_payload(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if not isinstance(raw_value, str):
        raise ValidationError("Claude result payload is neither a JSON object nor a JSON string.")

    candidate = raw_value.strip()
    fenced = _MARKDOWN_JSON_BLOCK_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        fenced_search = _MARKDOWN_JSON_BLOCK_SEARCH_RE.search(candidate)
        if fenced_search:
            candidate = fenced_search.group(1).strip()
    return json.loads(candidate)


def _map_claude_finding_category(category: str) -> str:
    mapping = {
        "correctness": "correctness",
        "security": "security",
        "test_gap": "test_gap",
        "testing": "test_gap",
        "scope": "scope",
        "documentation": "documentation",
        "consistency": "documentation",
        "completeness": "documentation",
        "artifact": "artifact",
        "process": "process",
        "manual_gate": "manual_gate",
        "external_dependency": "external_dependency",
    }
    return mapping.get(category, "other")


def _map_claude_finding_confidence(severity: str) -> str:
    if severity in {"critical", "high"}:
        return "high"
    if severity == "medium":
        return "medium"
    return "low"


def _normalize_alternate_claude_report(report: dict[str, Any]) -> dict[str, Any]:
    positive_signals: list[str] = []

    verification_review = report.get("verification_gate_review", {})
    if verification_review.get("status") == "accepted" and verification_review.get("notes"):
        positive_signals.append(str(verification_review["notes"]))

    scope_review = report.get("scope_compliance_review", {})
    if scope_review.get("status") == "accepted" and scope_review.get("notes"):
        positive_signals.append(str(scope_review["notes"]))

    content_review = report.get("content_review", {})
    for check in content_review.get("checks", []):
        if check.get("status") != "pass":
            continue
        requirement = str(check.get("requirement", "")).strip()
        evidence = str(check.get("evidence", "")).strip()
        if requirement and evidence:
            positive_signals.append(f"{requirement}: {evidence}")
        elif requirement:
            positive_signals.append(requirement)

    findings: list[dict[str, Any]] = []
    for finding in report.get("findings", []):
        severity = str(finding.get("severity", "low"))
        findings.append(
            {
                "finding_id": str(finding.get("id", "claude_finding")),
                "title": str(finding.get("title", "Claude audit finding")),
                "severity": severity if severity in {"critical", "high", "medium", "low", "info"} else "info",
                "category": _map_claude_finding_category(str(finding.get("category", "other"))),
                "confidence": _map_claude_finding_confidence(severity),
                "file_paths": [],
                "evidence": [
                    value
                    for value in [
                        str(finding.get("description", "")).strip(),
                        str(finding.get("impact", "")).strip(),
                    ]
                    if value
                ],
                "why_it_matters": str(finding.get("impact", "")).strip() or str(finding.get("description", "")).strip() or "Claude audit identified a risk.",
                "recommended_action": str(finding.get("recommendation", "")).strip() or "Review the Claude audit output and address the issue.",
                "is_blocking": severity in {"critical", "high"},
            }
        )

    next_state = str(report.get("next_recommended_state", "triage"))
    if next_state == "awaiting_human_gate":
        next_state = "pass"

    audited_artifacts = [
        path
        for path in scope_review.get("files_touched", [])
        if isinstance(path, str) and path
    ]

    return {
        "schema_version": "plan_orchestrator.audit_report.v1",
        "audit_lane": "claude",
        "item_id": str(report.get("item_id", "")),
        "attempt_number": int(report.get("attempt_number", 0)),
        "summary": str(report.get("summary", "")).strip(),
        "overall_verdict": {
            "pass": "pass",
            "issues_found": "issues_found",
            "blocked": "blocked",
            "inconclusive": "inconclusive",
            "fail": "issues_found",
        }.get(str(report.get("overall_result", "inconclusive")), "inconclusive"),
        "audited_artifacts": audited_artifacts,
        "positive_signals": positive_signals,
        "limitations": [str(value) for value in report.get("limitations", []) if str(value).strip()],
        "findings": findings,
        "next_recommended_state": next_state if next_state in {"pass", "triage", "blocked_external", "escalate"} else "triage",
    }


def _normalize_claude_error_envelope(
    *,
    item_id: str,
    attempt_number: int,
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    permission_denials = raw_payload.get("permission_denials", [])
    denial_summaries = []
    for denial in permission_denials:
        tool_name = str(denial.get("tool_name", "tool"))
        tool_input = denial.get("tool_input", {})
        if isinstance(tool_input, dict) and tool_input.get("path"):
            denial_summaries.append(f"{tool_name} denied for {tool_input['path']}")
        else:
            denial_summaries.append(f"{tool_name} denied")

    subtype = str(raw_payload.get("subtype", "unknown"))
    stop_reason = str(raw_payload.get("stop_reason", "unknown"))
    limitations = [
        f"Claude CLI returned subtype `{subtype}` with stop_reason `{stop_reason}` before producing a schema-valid audit report.",
    ]
    limitations.extend(denial_summaries)

    return {
        "schema_version": "plan_orchestrator.audit_report.v1",
        "audit_lane": "claude",
        "item_id": item_id,
        "attempt_number": attempt_number,
        "summary": "Claude audit ended inconclusively before producing a schema-valid report.",
        "overall_verdict": "inconclusive",
        "audited_artifacts": [],
        "positive_signals": [],
        "limitations": limitations,
        "findings": [],
        "next_recommended_state": "triage",
    }


def _normalize_claude_response_payload(raw_value: Any) -> dict[str, Any]:
    normalized = _parse_embedded_json_payload(raw_value)
    if isinstance(normalized, dict) and "audit_lane" not in normalized and normalized.get("auditor") == "claude":
        normalized = _normalize_alternate_claude_report(normalized)
    return normalized


def _normalize_claude_report(
    schema_path: Path,
    report_path: Path,
    *,
    item_id: str,
    attempt_number: int,
) -> dict[str, Any]:
    raw_text = report_path.read_text(encoding="utf-8", errors="replace")
    if not raw_text.strip():
        raise StageProcessError(f"claude -p produced empty output at {report_path.as_posix()}")
    try:
        raw_payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise StageProcessError(f"claude -p wrote invalid JSON output to {report_path.as_posix()}") from exc

    if not isinstance(raw_payload, dict):
        raise ValidationError("Claude output did not contain a schema-valid report or a result envelope.")

    if "structured_output" in raw_payload:
        normalized = _normalize_claude_response_payload(raw_payload["structured_output"])
        validate_named_schema(schema_path, normalized)
        write_json_atomic(report_path, normalized)
        return normalized

    if "schema_version" in raw_payload:
        validate_named_schema(schema_path, raw_payload)
        return raw_payload

    write_json_atomic(report_path.with_name(report_path.stem + ".raw.json"), raw_payload)

    if "result" not in raw_payload:
        normalized = _normalize_claude_error_envelope(
            item_id=item_id,
            attempt_number=attempt_number,
            raw_payload=raw_payload,
        )
        validate_named_schema(schema_path, normalized)
        write_json_atomic(report_path, normalized)
        return normalized

    normalized = _normalize_claude_response_payload(raw_payload["result"])
    validate_named_schema(schema_path, normalized)
    write_json_atomic(report_path, normalized)
    return normalized


def run_claude_audit(
    *,
    worktree_path: Path,
    prompt_path: Path,
    schema_path: Path,
    report_path: Path,
    stderr_log: Path,
    item_id: str,
    attempt_number: int,
    model: str,
    effort: str,
    max_turns: int,
    timeout_sec: int,
) -> StageResult:
    _require_command("claude")
    schema_json = json.dumps(json.loads(schema_path.read_text(encoding="utf-8")), separators=(",", ":"))

    return_code = _run_claude_once(
        worktree_path=worktree_path,
        prompt_path=prompt_path,
        schema_json=schema_json,
        report_path=report_path,
        stderr_log=stderr_log,
        model=model,
        effort=effort,
        max_turns=max_turns,
        timeout_sec=timeout_sec,
    )

    effort_used = effort
    if return_code != 0:
        stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace")
        if effort == "max" and _looks_like_unsupported_claude_max(stderr_text):
            retry_log = stderr_log.with_name(stderr_log.stem + ".attempt1" + stderr_log.suffix)
            stderr_log.replace(retry_log)
            return_code = _run_claude_once(
                worktree_path=worktree_path,
                prompt_path=prompt_path,
                schema_json=schema_json,
                report_path=report_path,
                stderr_log=stderr_log,
                model=model,
                effort="high",
                max_turns=max_turns,
                timeout_sec=timeout_sec,
            )
            effort_used = "high"

    if return_code != 0:
        raise StageProcessError(
            f"claude -p failed with exit code {return_code}. See {stderr_log.as_posix()}"
        )

    report = _normalize_claude_report(
        schema_path,
        report_path,
        item_id=item_id,
        attempt_number=attempt_number,
    )
    return StageResult(
        report=report,
        report_path=report_path,
        stdout_log=None,
        stderr_log=stderr_log,
        effort_used=effort_used,
    )
