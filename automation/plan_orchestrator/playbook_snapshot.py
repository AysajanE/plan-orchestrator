from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .models import NormalizedPlan
from .playbook_parser import parse_playbook


def normalized_plan_from_playbook_snapshot(
    *,
    snapshot_path: Path,
    preserved_playbook_path: Path,
    normalize_parsed_playbook: Callable[[dict, Path], NormalizedPlan],
    missing_error: str,
    malformed_error: str,
) -> NormalizedPlan:
    if not snapshot_path.exists():
        raise RuntimeError(missing_error)

    snapshot_text = snapshot_path.read_text(encoding="utf-8")
    try:
        playbook_source = snapshot_text.split("\n---\n\n", 1)[1]
    except IndexError as exc:
        raise RuntimeError(malformed_error) from exc

    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as handle:
        handle.write(playbook_source)
        temp_path = Path(handle.name)

    try:
        parsed = parse_playbook(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return normalize_parsed_playbook(parsed, preserved_playbook_path)
