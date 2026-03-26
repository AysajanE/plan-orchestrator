from __future__ import annotations

from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    note_path = Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "status_note.md"
    text = note_path.read_text(encoding="utf-8")

    require(
        "Pending external evidence." not in text,
        "status note still contains the draft placeholder",
    )
    require("healthy" in text.lower(), "status note must report the provider state")
    print("status note verification passed")


if __name__ == "__main__":
    main()
