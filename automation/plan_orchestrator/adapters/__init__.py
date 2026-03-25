from __future__ import annotations

from pathlib import Path

from .base import BasePlanAdapter, PlanAdapter
from .markdown_playbook import MarkdownPlaybookAdapter


def build_default_adapter(repo_root: Path) -> BasePlanAdapter:
    return MarkdownPlaybookAdapter(repo_root)


__all__ = [
    "BasePlanAdapter",
    "PlanAdapter",
    "MarkdownPlaybookAdapter",
    "build_default_adapter",
]
