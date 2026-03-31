from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

from automation.plan_orchestrator.config import default_runtime_options
from automation.plan_orchestrator.subprocess_runner import run_claude_audit, run_codex_stage
from automation.plan_orchestrator.validators import ValidationError, validate_named_schema
from automation.plan_orchestrator.verification import run_verification


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


def make_item_context(
    allowed_write_roots: list[str],
    artifact_checks: list[dict],
    command_groups: list[dict],
    *,
    artifact_inputs: Optional[list[dict]] = None,
) -> dict:
    return {
        "schema_version": "plan_orchestrator.item_context.v1",
        "generated_at_utc": "2026-03-25T12:00:00Z",
        "run_id": "RUN_TEST",
        "adapter_id": "markdown_playbook_v1",
        "item": {
            "item_id": "01",
            "phase": "release note",
            "phase_slug": "release-note",
            "action": "Verify the change.",
            "owner_type": "operator",
            "deliverable": "docs/runbooks/release_note.md",
            "exit_criteria": "Deliverable exists.",
            "change_profile": "docs_only",
            "requires_red_green": any(group.get("required", False) for group in command_groups),
            "manual_gate_required": False,
            "external_check_required": False,
        },
        "worktree": {
            "path": ".",
            "branch_name": "orchestrator/item/RUN_TEST/01/attempt-1",
            "base_ref": "deadbeef",
            "head_ref": "deadbeef",
            "workspace_packet_root": ".local/plan_orchestrator/packet",
        },
        "stage_context": {
            "stage": "execute",
            "attempt_number": 1,
            "fix_round_index": 0,
            "remediation_round_index": 0,
            "prior_checkpoint_ref": None,
            "terminal_targets": ["passed", "awaiting_human_gate", "blocked_external", "escalated"],
            "notes": [],
        },
        "repo_scope": {
            "consult_paths": [],
            "allowed_write_roots": allowed_write_roots,
            "forbidden_roots": [".git"],
            "scope_notes": [],
        },
        "source_of_truth_paths": [],
        "sensitive_path_globs": [],
        "support_sections": [],
        "artifact_inputs": artifact_inputs or [],
        "verification_plan": {
            "artifact_checks": artifact_checks,
            "command_groups": command_groups,
        },
    }


def make_claude_audit_report(*, summary: str = "Looks good.") -> dict:
    return {
        "schema_version": "plan_orchestrator.audit_report.v1",
        "audit_lane": "claude",
        "item_id": "01",
        "attempt_number": 1,
        "summary": summary,
        "overall_verdict": "pass",
        "audited_artifacts": ["artifact.json"],
        "positive_signals": ["signal"],
        "limitations": ["none"],
        "findings": [],
        "next_recommended_state": "pass",
    }


def template_placeholders(path: Path) -> list[str]:
    return sorted(set(re.findall(r"{{([A-Z0-9_]+)}}", path.read_text(encoding="utf-8"))))


def prompt_text(name: str) -> str:
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / "automation" / "plan_orchestrator" / "prompts" / name).read_text(encoding="utf-8")


class VerificationTests(unittest.TestCase):
    def test_default_runtime_options_include_verification_timeout(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"PLAN_ORCHESTRATOR_VERIFICATION_TIMEOUT_SEC": "17"},
            clear=False,
        ):
            options = default_runtime_options(auto_advance=False, max_items=None)

        self.assertEqual(options.verification_timeout_sec, 17)

    def test_run_state_schema_requires_verification_timeout(self) -> None:
        payload = {
            "schema_version": "plan_orchestrator.run_state.v1",
            "run_id": "RUN_TEST",
            "adapter_id": "markdown_playbook_v1",
            "repo_root": "/tmp/repo",
            "playbook_source_path": "playbook.md",
            "playbook_source_sha256": "a" * 64,
            "normalized_plan_path": "normalized_plan.json",
            "base_head_sha": "abcdef1",
            "run_branch_name": "orchestrator/run/RUN_TEST",
            "created_at_utc": "2026-03-25T12:00:00Z",
            "updated_at_utc": "2026-03-25T12:00:00Z",
            "current_state": "ST05_PLAN_NORMALIZED",
            "current_item_id": None,
            "options": {
                "codex_model": "gpt-5.4",
                "codex_reasoning_effort": "xhigh",
                "claude_model": "opus",
                "claude_effort": "max",
                "auto_advance": False,
                "max_items": None,
                "max_fix_rounds": 2,
                "max_remediation_rounds": 1,
                "execution_timeout_sec": 10,
                "audit_timeout_sec": 10,
                "triage_timeout_sec": 10,
                "fix_timeout_sec": 10,
                "remediation_timeout_sec": 10
            },
            "items": [],
            "event_log": []
        }

        with self.assertRaises(ValidationError):
            validate_named_schema("run_state.schema.json", payload)

    def test_docs_only_verification_passes_when_required_artifact_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            artifact = repo_root / "docs" / "runbooks" / "release_note.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("# Release note\n", encoding="utf-8")
            head = git_commit_all(repo_root, "initial")

            report = run_verification(
                repo_root=repo_root,
                worktree_path=repo_root,
                item_context=make_item_context(
                    ["docs/runbooks"],
                    [
                        {
                            "path": "docs/runbooks/release_note.md",
                            "check_kind": "exists",
                            "expected_values": [],
                            "reason": "Required deliverable artifact exists.",
                        }
                    ],
                    [],
                ),
                previous_ref=head,
                current_ref=head,
                report_path=repo_root / ".local" / "verification_report.json",
                logs_dir=repo_root / ".local" / "logs",
                timeout_sec=60,
            )

            self.assertEqual(report["overall_result"], "pass")
            self.assertEqual(report["next_recommended_state"], "audit")

    def test_required_verification_command_timeout_fails_and_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            (repo_root / "README.md").write_text("baseline\n", encoding="utf-8")
            head = git_commit_all(repo_root, "initial")

            def fake_run(*args, **kwargs):
                command = kwargs.get("args", args[0])
                raise subprocess.TimeoutExpired(cmd=command, timeout=1)

            with mock.patch("automation.plan_orchestrator.verification.subprocess.run", side_effect=fake_run), mock.patch(
                "automation.plan_orchestrator.verification.scope_check_for_committed_changes",
                return_value=("All committed changes are within allowed write roots.", []),
            ):
                report = run_verification(
                    repo_root=repo_root,
                    worktree_path=repo_root,
                    item_context=make_item_context(
                        ["src"],
                        [],
                        [{"label": "tests", "commands": ["sleep 10"], "required": True}],
                    ),
                    previous_ref=head,
                    current_ref=head,
                    report_path=repo_root / ".local" / "verification_report.json",
                    logs_dir=repo_root / ".local" / "logs",
                    timeout_sec=1,
                )

            self.assertEqual(report["overall_result"], "fail")
            self.assertEqual(report["next_recommended_state"], "fix")
            self.assertEqual(report["command_results"][0]["failure_kind"], "timeout")
            self.assertEqual(report["command_results"][0]["timeout_sec"], 1)

    def test_optional_command_failure_is_partial_not_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            (repo_root / "README.md").write_text("baseline\n", encoding="utf-8")
            head = git_commit_all(repo_root, "initial")

            report = run_verification(
                repo_root=repo_root,
                worktree_path=repo_root,
                item_context=make_item_context(
                    ["src"],
                    [],
                    [{"label": "optional_demo", "commands": ["false"], "required": False}],
                ),
                previous_ref=head,
                current_ref=head,
                report_path=repo_root / ".local" / "verification_report.json",
                logs_dir=repo_root / ".local" / "logs",
                timeout_sec=60,
            )

            self.assertEqual(report["overall_result"], "partial")
            self.assertEqual(report["next_recommended_state"], "audit")
            self.assertFalse(report["command_results"][0]["required"])

    def test_scope_violation_recommends_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            init_git_repo(repo_root)
            tracked = repo_root / "src" / "safe.py"
            tracked.parent.mkdir(parents=True, exist_ok=True)
            tracked.write_text("value = 1\n", encoding="utf-8")
            previous_ref = git_commit_all(repo_root, "initial")

            out_of_scope = repo_root / "scripts" / "leak.py"
            out_of_scope.parent.mkdir(parents=True, exist_ok=True)
            out_of_scope.write_text("print('oops')\n", encoding="utf-8")
            current_ref = git_commit_all(repo_root, "out-of-scope change")

            report = run_verification(
                repo_root=repo_root,
                worktree_path=repo_root,
                item_context=make_item_context(["src"], [], []),
                previous_ref=previous_ref,
                current_ref=current_ref,
                report_path=repo_root / ".local" / "verification_report.json",
                logs_dir=repo_root / ".local" / "logs",
                timeout_sec=60,
            )

            self.assertEqual(report["overall_result"], "fail")
            self.assertEqual(report["next_recommended_state"], "escalate")
            self.assertEqual(report["scope_check"]["status"], "fail")
            self.assertIn("scripts/leak.py", report["scope_check"]["out_of_scope_paths"])

    def test_run_codex_stage_uses_expected_exec_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "prompt.md"
            schema_path = root / "schema.json"
            report_path = root / "report.json"
            stdout_log = root / "stdout.log"
            stderr_log = root / "stderr.log"
            prompt_path.write_text("Do the thing.\n", encoding="utf-8")
            schema_path.write_text('{"type":"object"}\n', encoding="utf-8")

            captured = {}

            def fake_run(**kwargs):
                captured["argv"] = kwargs["argv"]
                return 0

            with mock.patch("automation.plan_orchestrator.subprocess_runner._require_command"), mock.patch(
                "automation.plan_orchestrator.subprocess_runner._run",
                side_effect=fake_run,
            ), mock.patch(
                "automation.plan_orchestrator.subprocess_runner.validate_json_file",
                return_value={"ok": True},
            ):
                result = run_codex_stage(
                    worktree_path=root,
                    prompt_path=prompt_path,
                    schema_path=schema_path,
                    report_path=report_path,
                    stdout_log=stdout_log,
                    stderr_log=stderr_log,
                    model="gpt-5.4",
                    reasoning_effort="xhigh",
                    sandbox="workspace-write",
                    timeout_sec=60,
                )

            self.assertEqual(result.report, {"ok": True})
            self.assertEqual(
                captured["argv"],
                [
                    "codex",
                    "exec",
                    "-C",
                    str(root),
                    "-m",
                    "gpt-5.4",
                    "-s",
                    "workspace-write",
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(report_path),
                    "--ephemeral",
                    "-c",
                    "model_reasoning_effort=xhigh",
                    "-c",
                    "web_search=disabled",
                    "-",
                ],
            )

    def test_run_codex_stage_sanitizes_shell_and_git_override_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "prompt.md"
            schema_path = root / "schema.json"
            report_path = root / "report.json"
            stdout_log = root / "stdout.log"
            stderr_log = root / "stderr.log"
            prompt_path.write_text("Do the thing.\n", encoding="utf-8")
            schema_path.write_text('{"type":"object"}\n', encoding="utf-8")

            captured = {}

            def fake_subprocess_run(*args, **kwargs):
                captured["env"] = kwargs["env"]
                return subprocess.CompletedProcess(args[0], 0, "", "")

            with mock.patch.dict(
                os.environ,
                {
                    "PATH": "/usr/bin",
                    "OPENAI_API_KEY": "secret",
                    "BASH_ENV": "/tmp/bashrc",
                    "ENV": "/tmp/shrc",
                    "PROMPT_COMMAND": "echo nope",
                    "CDPATH": "/tmp",
                    "GIT_DIR": "/tmp/not-this-repo",
                    "GIT_WORK_TREE": "/tmp/not-this-worktree",
                    "GIT_INDEX_FILE": "/tmp/index",
                },
                clear=True,
            ), mock.patch(
                "automation.plan_orchestrator.subprocess_runner._require_command"
            ), mock.patch(
                "automation.plan_orchestrator.subprocess_runner.subprocess.run",
                side_effect=fake_subprocess_run,
            ), mock.patch(
                "automation.plan_orchestrator.subprocess_runner.validate_json_file",
                return_value={"ok": True},
            ):
                run_codex_stage(
                    worktree_path=root,
                    prompt_path=prompt_path,
                    schema_path=schema_path,
                    report_path=report_path,
                    stdout_log=stdout_log,
                    stderr_log=stderr_log,
                    model="gpt-5.4",
                    reasoning_effort="xhigh",
                    sandbox="workspace-write",
                    timeout_sec=60,
                )

        self.assertEqual(captured["env"]["PATH"], "/usr/bin")
        self.assertEqual(captured["env"]["OPENAI_API_KEY"], "secret")
        self.assertEqual(captured["env"]["PLAN_ORCHESTRATOR_STAGE_RUNNER"], "1")
        self.assertEqual(captured["env"]["PLAN_ORCHESTRATOR_STAGE_TOOL"], "codex")
        for forbidden in (
            "BASH_ENV",
            "ENV",
            "PROMPT_COMMAND",
            "CDPATH",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
        ):
            self.assertNotIn(forbidden, captured["env"])

    def test_run_claude_audit_extracts_structured_output(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        schema_path = repo_root / "automation" / "plan_orchestrator" / "schemas" / "audit_report.schema.json"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "prompt.md"
            report_path = root / "claude_audit.json"
            stderr_log = root / "claude.stderr.log"
            prompt_path.write_text("Audit.\n", encoding="utf-8")

            envelope = {
                "type": "result",
                "subtype": "success",
                "structured_output": make_claude_audit_report(summary="Structured output wins."),
                "result": "Legacy prose should be ignored."
            }

            def fake_run_once(**kwargs):
                Path(kwargs["report_path"]).write_text(json.dumps(envelope), encoding="utf-8")
                Path(kwargs["stderr_log"]).write_text("", encoding="utf-8")
                return 0

            with mock.patch("automation.plan_orchestrator.subprocess_runner._require_command"), mock.patch(
                "automation.plan_orchestrator.subprocess_runner._run_claude_once",
                side_effect=fake_run_once,
            ):
                result = run_claude_audit(
                    worktree_path=root,
                    prompt_path=prompt_path,
                    schema_path=schema_path,
                    report_path=report_path,
                    stderr_log=stderr_log,
                    item_id="01",
                    attempt_number=1,
                    model="opus",
                    effort="max",
                    max_turns=8,
                    timeout_sec=60,
                )

            self.assertEqual(result.report["summary"], "Structured output wins.")
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["summary"], "Structured output wins.")

    def test_run_claude_audit_sanitizes_shell_and_git_override_env(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        schema_path = repo_root / "automation" / "plan_orchestrator" / "schemas" / "audit_report.schema.json"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "prompt.md"
            report_path = root / "claude_audit.json"
            stderr_log = root / "claude.stderr.log"
            prompt_path.write_text("Audit.\n", encoding="utf-8")

            envelope = {
                "type": "result",
                "subtype": "success",
                "structured_output": make_claude_audit_report(summary="Structured output wins."),
                "result": "Legacy prose should be ignored."
            }
            captured = {}

            def fake_subprocess_run(*args, **kwargs):
                captured["env"] = kwargs["env"]
                kwargs["stdout"].write(json.dumps(envelope))
                kwargs["stderr"].write("")
                return subprocess.CompletedProcess(args[0], 0, "", "")

            with mock.patch.dict(
                os.environ,
                {
                    "PATH": "/usr/bin",
                    "ANTHROPIC_API_KEY": "secret",
                    "BASH_ENV": "/tmp/bashrc",
                    "ENV": "/tmp/shrc",
                    "PROMPT_COMMAND": "echo nope",
                    "CDPATH": "/tmp",
                    "GIT_DIR": "/tmp/not-this-repo",
                    "GIT_WORK_TREE": "/tmp/not-this-worktree",
                    "GIT_INDEX_FILE": "/tmp/index",
                },
                clear=True,
            ), mock.patch(
                "automation.plan_orchestrator.subprocess_runner._require_command"
            ), mock.patch(
                "automation.plan_orchestrator.subprocess_runner.subprocess.run",
                side_effect=fake_subprocess_run,
            ):
                run_claude_audit(
                    worktree_path=root,
                    prompt_path=prompt_path,
                    schema_path=schema_path,
                    report_path=report_path,
                    stderr_log=stderr_log,
                    item_id="01",
                    attempt_number=1,
                    model="opus",
                    effort="max",
                    max_turns=8,
                    timeout_sec=60,
                )

        self.assertEqual(captured["env"]["PATH"], "/usr/bin")
        self.assertEqual(captured["env"]["ANTHROPIC_API_KEY"], "secret")
        self.assertEqual(captured["env"]["PLAN_ORCHESTRATOR_STAGE_RUNNER"], "1")
        self.assertEqual(captured["env"]["PLAN_ORCHESTRATOR_STAGE_TOOL"], "claude")
        for forbidden in (
            "BASH_ENV",
            "ENV",
            "PROMPT_COMMAND",
            "CDPATH",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
        ):
            self.assertNotIn(forbidden, captured["env"])

    def test_execution_prompt_mentions_artifact_manifest_and_non_red_green_guidance(self) -> None:
        prompt = prompt_text("execution_codex.md")

        self.assertIn("{{ARTIFACT_MANIFEST_WORKSPACE_PATH}}", prompt)
        self.assertIn("placeholder evidence strings", prompt)
        self.assertIn("files_touched", prompt)
        self.assertIn("verification byproducts", prompt)

    def test_audit_prompts_define_authoritative_packet_and_support_reads(self) -> None:
        for prompt_name in ("audit_codex.md", "audit_claude.md"):
            with self.subTest(prompt_name=prompt_name):
                prompt = prompt_text(prompt_name)
                self.assertIn("authoritative", prompt)
                self.assertIn("candidate patch", prompt)
                self.assertIn("ignore any live worktree dirtiness", prompt)
                self.assertIn("{{PLAYBOOK_SNAPSHOT_WORKSPACE_PATH}}", prompt)
                self.assertIn("{{NORMALIZED_PLAN_WORKSPACE_PATH}}", prompt)

    def test_triage_prompt_defines_next_stage_mapping(self) -> None:
        prompt = prompt_text("triage_codex.md")

        for expected in (
            'pass -> next_stage="passed"',
            'fix_required -> next_stage="fix"',
            'awaiting_human_gate -> next_stage="awaiting_human_gate"',
            'blocked_external -> next_stage="blocked_external"',
            'escalate -> next_stage="escalated"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)

    def test_fix_and_remediation_prompts_define_stage_and_next_state_rules(self) -> None:
        fix_prompt = prompt_text("fix_codex.md")
        remediation_prompt = prompt_text("remediation_codex.md")

        self.assertIn('set `stage` to `"fix"`', fix_prompt)
        self.assertIn("`next_recommended_state`", fix_prompt)
        self.assertIn("placeholder evidence strings", fix_prompt)

        self.assertIn('set `stage` to `"remediation"`', remediation_prompt)
        self.assertIn("`next_recommended_state`", remediation_prompt)
        self.assertIn("placeholder evidence strings", remediation_prompt)
        self.assertIn("differs from prior rounds", remediation_prompt)

    def test_prompt_manifest_matches_runtime_templates_and_has_no_orphans(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        prompts_root = repo_root / "automation" / "plan_orchestrator" / "prompts"
        manifest = json.loads((prompts_root / "prompt_manifest.json").read_text(encoding="utf-8"))

        for entry in manifest["prompts"]:
            template_path = repo_root / entry["path"]
            self.assertTrue(template_path.exists(), msg=entry["name"])
            self.assertEqual(
                template_placeholders(template_path),
                sorted(entry["placeholders"]),
                msg=entry["name"],
            )
