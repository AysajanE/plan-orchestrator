from __future__ import annotations

from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    note_path = Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "release_note.md"
    text = note_path.read_text(encoding="utf-8")

    require(
        "Pending launch-note update." not in text,
        "release note still contains the draft placeholder",
    )
    require("Plan Orchestrator" in text, "release note must mention Plan Orchestrator")
    print("release note verification passed")


if __name__ == "__main__":
    main()
