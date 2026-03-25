import json
import tempfile
import unittest
from pathlib import Path

from automation.plan_orchestrator.reporting import (
    artifact_spec,
    build_artifact_manifest,
    workspace_path_for_manifest_entry,
)
from automation.plan_orchestrator.validators import compute_path_sha256


class ArtifactManifestTests(unittest.TestCase):
    def test_build_artifact_manifest_copies_local_artifacts_into_workspace_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            tracked = repo_root / "docs" / "runbooks" / "demo_mode_lock.md"
            local_artifact = repo_root / ".local" / "automation" / "plan_orchestrator" / "runs" / "RUN1" / "item_context.json"
            worktree_root = repo_root / "worktree"
            packet_root = worktree_root / ".local" / "plan_orchestrator" / "packet"
            output_path = repo_root / ".local" / "automation" / "plan_orchestrator" / "runs" / "RUN1" / "artifact_manifest.json"

            tracked.parent.mkdir(parents=True, exist_ok=True)
            local_artifact.parent.mkdir(parents=True, exist_ok=True)
            worktree_root.mkdir(parents=True, exist_ok=True)

            tracked.write_text("# Demo mode lock\n", encoding="utf-8")
            local_artifact.write_text('{"stage":"execute"}\n', encoding="utf-8")

            specs = [
                artifact_spec(
                    logical_name="demo_lock",
                    path="docs/runbooks/demo_mode_lock.md",
                    content_type="markdown",
                    storage_class="tracked_repo",
                    git_policy="tracked",
                    trust_level="source_input",
                    producer="normalize_plan",
                    consumers=["execute"],
                    must_exist=True,
                    description="Tracked demo lock.",
                ),
                artifact_spec(
                    logical_name="item_context",
                    path=".local/automation/plan_orchestrator/runs/RUN1/item_context.json",
                    content_type="json",
                    storage_class="local_run_control",
                    git_policy="gitignored",
                    trust_level="orchestrator_generated",
                    producer="prepare_context",
                    consumers=["execute"],
                    must_exist=True,
                    description="Context packet.",
                ),
            ]

            manifest, manifest_workspace = build_artifact_manifest(
                repo_root=repo_root,
                worktree_root=worktree_root,
                packet_root=packet_root,
                run_id="RUN1",
                item_id="01",
                attempt_number=1,
                producer_stage="execute",
                artifact_specs=specs,
                output_path=output_path,
            )

            self.assertEqual(manifest_workspace, ".local/plan_orchestrator/packet/artifact_manifest.json")
            tracked_entry = next(entry for entry in manifest["artifacts"] if entry["logical_name"] == "demo_lock")
            local_entry = next(entry for entry in manifest["artifacts"] if entry["logical_name"] == "item_context")

            self.assertEqual(tracked_entry["workspace_packet_path"], "docs/runbooks/demo_mode_lock.md")
            self.assertEqual(tracked_entry["visibility_bridge"], "direct-worktree-path")

            copied_local_path = worktree_root / local_entry["workspace_packet_path"]
            self.assertTrue(copied_local_path.exists())
            self.assertEqual(local_entry["sha256"], compute_path_sha256(local_artifact))
            self.assertEqual(
                workspace_path_for_manifest_entry(manifest, "item_context"),
                local_entry["workspace_packet_path"],
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["item_id"], "01")
