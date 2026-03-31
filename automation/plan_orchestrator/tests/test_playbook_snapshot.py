from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.plan_orchestrator.adapters import build_default_adapter
from automation.plan_orchestrator.playbook_snapshot import normalized_plan_from_playbook_snapshot
from automation.plan_orchestrator.reporting import write_playbook_snapshot
from automation.plan_orchestrator.tests.support import write_minimal_playbook


class PlaybookSnapshotTests(unittest.TestCase):
    def test_normalized_plan_from_playbook_snapshot_rebuilds_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            playbook_path = repo_root / "playbook.md"
            write_minimal_playbook(playbook_path)
            snapshot_path = repo_root / "playbook_source_snapshot.md"
            write_playbook_snapshot(
                source_path=playbook_path,
                source_sha256="a" * 64,
                source_text=playbook_path.read_text(encoding="utf-8"),
                output_path=snapshot_path,
            )

            adapter = build_default_adapter(repo_root)
            plan = normalized_plan_from_playbook_snapshot(
                snapshot_path=snapshot_path,
                preserved_playbook_path=Path("playbook.md"),
                normalize_parsed_playbook=adapter.normalize,
                missing_error=f"Missing playbook snapshot for refresh: {snapshot_path}",
                malformed_error=f"Playbook snapshot is malformed and cannot be refreshed: {snapshot_path}",
            )

        self.assertEqual(plan.adapter_id, "markdown_playbook_v1")
        self.assertEqual([item.item_id for item in plan.items], ["01"])
        self.assertEqual(plan.plan_source["path"], "playbook.md")

    def test_normalized_plan_from_playbook_snapshot_rejects_malformed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            snapshot_path = repo_root / "playbook_source_snapshot.md"
            snapshot_path.write_text("not-a-real-snapshot\n", encoding="utf-8")
            adapter = build_default_adapter(repo_root)

            with self.assertRaisesRegex(RuntimeError, "cannot be refreshed"):
                normalized_plan_from_playbook_snapshot(
                    snapshot_path=snapshot_path,
                    preserved_playbook_path=Path("playbook.md"),
                    normalize_parsed_playbook=adapter.normalize,
                    missing_error=f"Missing playbook snapshot for refresh: {snapshot_path}",
                    malformed_error=f"Playbook snapshot is malformed and cannot be refreshed: {snapshot_path}",
                )
