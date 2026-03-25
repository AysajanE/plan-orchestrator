from __future__ import annotations

from pathlib import Path

from automation.plan_orchestrator.adapters import MarkdownPlaybookAdapter


class ExampleMarkdownPlaybookAdapter(MarkdownPlaybookAdapter):
    """Thin example wrapper around the canonical markdown adapter.

    This class exists only to show where a future project-specific wrapper would
    hook into the public engine. It does not introduce a second public input
    contract and it does not change the runtime loop.
    """

    adapter_id = "example_markdown_playbook_v1"

    def __init__(self, repo_root: Path) -> None:
        super().__init__(repo_root)


def build_adapter(repo_root: str | Path) -> ExampleMarkdownPlaybookAdapter:
    return ExampleMarkdownPlaybookAdapter(Path(repo_root))
