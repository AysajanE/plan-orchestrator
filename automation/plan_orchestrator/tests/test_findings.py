import tempfile
import unittest
from pathlib import Path

from automation.plan_orchestrator.findings import canonical_finding_id, merge_findings, write_merged_findings_packet
from automation.plan_orchestrator.validators import validate_named_schema


class FindingsTests(unittest.TestCase):
    def test_canonical_finding_id_is_order_independent(self) -> None:
        left = canonical_finding_id(
            title="Scope violation",
            category="scope",
            file_paths=["b.py", "a.py", "a.py"],
            evidence=["line 2", "line 1"],
        )
        right = canonical_finding_id(
            title="Scope violation",
            category="scope",
            file_paths=["a.py", "b.py"],
            evidence=["line 1", "line 2", "line 1"],
        )

        self.assertEqual(left, right)

    def test_merge_findings_dedupes_duplicate_audit_findings(self) -> None:
        shared = {
            "finding_id": "audit-1",
            "title": "Bad scope",
            "severity": "high",
            "category": "scope",
            "file_paths": ["src/app.py"],
            "evidence": ["changed src/app.py"],
            "recommended_action": "Narrow the scope.",
            "why_it_matters": "The change escaped allowed roots.",
            "is_blocking": True,
        }
        verification_report = {
            "summary": "Verification passed.",
            "command_results": [],
            "artifact_checks": [],
            "scope_check": {"status": "pass", "out_of_scope_paths": [], "note": "ok"},
        }
        codex_audit_report = {"summary": "Codex found one issue.", "findings": [shared]}
        claude_audit_report = {"summary": "Claude found the same issue.", "findings": [{**shared, "finding_id": "audit-2"}]}

        merged, suppressed = merge_findings(
            verification_report=verification_report,
            codex_audit_report=codex_audit_report,
            claude_audit_report=claude_audit_report,
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["source_refs"]), 2)
        self.assertEqual(merged[0]["file_paths"], ["src/app.py"])
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0]["suppression_reason"], f"duplicate_of:{merged[0]['canonical_id']}")

    def test_write_merged_findings_packet_includes_verification_failures(self) -> None:
        verification_report = {
            "summary": "Verification failed.",
            "command_results": [
                {
                    "label": "tests#1",
                    "command": "forge test",
                    "exit_code": 1,
                    "status": "fail",
                    "required": True,
                    "log_path": ".local/logs/tests.log",
                }
            ],
            "artifact_checks": [],
            "scope_check": {
                "status": "fail",
                "out_of_scope_paths": ["src/outside_scope.py"],
                "note": "Committed changes include out-of-scope paths.",
            },
        }
        codex_audit_report = {"summary": "No findings.", "findings": []}
        claude_audit_report = {"summary": "No findings.", "findings": []}

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "merged.json"
            packet = write_merged_findings_packet(
                output_path=output_path,
                item_id="06",
                attempt_number=1,
                verification_report=verification_report,
                codex_audit_report=codex_audit_report,
                claude_audit_report=claude_audit_report,
            )

        categories = {finding["category"] for finding in packet["merged_findings"]}
        self.assertIn("correctness", categories)
        self.assertIn("scope", categories)
        self.assertEqual(packet["item_id"], "06")

    def test_merge_findings_ignores_optional_verification_command_failures(self) -> None:
        verification_report = {
            "summary": "Verification completed with optional command failures.",
            "command_results": [
                {
                    "label": "optional_demo#1",
                    "command": "false",
                    "exit_code": 1,
                    "status": "fail",
                    "required": False,
                    "log_path": ".local/logs/optional.log",
                }
            ],
            "artifact_checks": [],
            "scope_check": {"status": "pass", "out_of_scope_paths": [], "note": "ok"},
        }
        merged, suppressed = merge_findings(
            verification_report=verification_report,
            codex_audit_report={"summary": "No findings.", "findings": []},
            claude_audit_report={"summary": "No findings.", "findings": []},
        )

        self.assertEqual(merged, [])
        self.assertEqual(suppressed, [])

    def test_write_merged_findings_packet_includes_mutation_control_signals(self) -> None:
        mutation_report = {
            "stage": "fix",
            "summary": "Waiting on a product decision before the checksum format can be finalized.",
            "verdict": "needs_human_input",
            "residual_open_items": ["Product owner must confirm the checksum output format."],
            "residual_risks": ["Passing now would lock in the wrong checksum format."],
            "next_recommended_state": "awaiting_human_gate",
        }
        verification_report = {
            "summary": "Verification passed.",
            "command_results": [],
            "artifact_checks": [],
            "scope_check": {"status": "pass", "out_of_scope_paths": [], "note": "ok"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            packet = write_merged_findings_packet(
                output_path=Path(tmp) / "merged.json",
                item_id="06",
                attempt_number=1,
                mutation_report=mutation_report,
                verification_report=verification_report,
                codex_audit_report={"summary": "No findings.", "findings": []},
                claude_audit_report={"summary": "No findings.", "findings": []},
            )

        self.assertEqual(packet["mutation_report_control"]["signals"], ["needs_human_input", "unresolved_items"])
        mutation_source_ids = {
            source_ref["source_id"]
            for finding in packet["merged_findings"]
            for source_ref in finding["source_refs"]
            if source_ref["source_type"] == "mutation_report"
        }
        self.assertIn("mutation_control_needs_human_input", mutation_source_ids)
        self.assertIn("mutation_control_unresolved_items", mutation_source_ids)
        self.assertTrue(
            any(
                finding["disposition"] == "requires_human_judgment"
                for finding in packet["merged_findings"]
                if any(source_ref["source_type"] == "mutation_report" for source_ref in finding["source_refs"])
            )
        )

    def test_triage_report_schema_accepts_preserved_file_paths(self) -> None:
        payload = {
            "schema_version": "plan_orchestrator.triage_report.v1",
            "stage": "triage",
            "item_id": "01",
            "attempt_number": 1,
            "summary": "Fix required.",
            "overall_decision": "fix_required",
            "reasoning_notes": [],
            "merged_findings": [
                {
                    "canonical_id": "finding_1234",
                    "title": "Disclosure text drift",
                    "severity": "medium",
                    "category": "documentation",
                    "disposition": "actionable",
                    "recommended_owner": "codex_fix",
                    "source_refs": [{"source_type": "codex_audit", "source_id": "audit_1"}],
                    "file_paths": ["docs/runbooks/demo_mode_lock.md"],
                    "evidence": ["Disclosure text does not match checklist."],
                    "recommended_action": "Update the document to use the frozen disclosure sentence.",
                    "acceptance_check": "Document uses the checklist sentence verbatim.",
                    "is_blocking": True,
                }
            ],
            "suppressed_findings": [],
            "next_stage": "fix",
        }

        validate_named_schema("triage_report.schema.json", payload)

    def test_triage_and_manual_gate_schemas_accept_mutation_report_source_refs(self) -> None:
        finding = {
            "canonical_id": "finding_1234",
            "title": "Mutation stage requested human input",
            "severity": "high",
            "category": "manual_gate",
            "disposition": "requires_human_judgment",
            "recommended_owner": "human",
            "source_refs": [{"source_type": "mutation_report", "source_id": "mutation_control_needs_human_input"}],
            "file_paths": [],
            "evidence": ["Need product approval for checksum format."],
            "recommended_action": "Collect the product decision before passing the item.",
            "acceptance_check": "The checksum format is approved and no mutation-stage human-input signal remains.",
            "is_blocking": True,
        }
        triage_payload = {
            "schema_version": "plan_orchestrator.triage_report.v1",
            "stage": "triage",
            "item_id": "01",
            "attempt_number": 1,
            "summary": "Awaiting human input.",
            "overall_decision": "awaiting_human_gate",
            "reasoning_notes": [],
            "merged_findings": [finding],
            "suppressed_findings": [],
            "next_stage": "awaiting_human_gate",
        }
        manual_gate_payload = {
            "schema_version": "plan_orchestrator.manual_gate.v1",
            "run_id": "RUN_TEST",
            "item_id": "01",
            "gate_id": "gate_01",
            "gate_type": "approval",
            "status": "pending",
            "requested_by": "orchestrator",
            "requested_at_utc": "2026-03-15T00:00:00Z",
            "requested_reason": "Need human approval.",
            "required_evidence": ["approval record"],
            "review_findings": [finding],
            "related_refs": {
                "branch_name": "orchestrator/item/RUN_TEST/01/attempt-1",
                "worktree_path": "tmp/worktree",
                "checkpoint_ref": "checkpoint-ref",
                "artifact_manifest_path": "runs/RUN_TEST/items/01/attempt-1/artifact_manifest.json",
                "triage_report_path": "runs/RUN_TEST/reports/items/01/attempt-1/triage_report.json",
                "merged_findings_packet_path": "runs/RUN_TEST/items/01/attempt-1/merged_findings_packet.json",
                "codex_audit_report_path": "runs/RUN_TEST/reports/items/01/attempt-1/codex_audit_report.json",
                "claude_audit_report_path": "runs/RUN_TEST/reports/items/01/attempt-1/claude_audit_report.json",
            },
            "decision": None,
        }

        validate_named_schema("triage_report.schema.json", triage_payload)
        validate_named_schema("manual_gate.schema.json", manual_gate_payload)
