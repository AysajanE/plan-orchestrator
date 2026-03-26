from __future__ import annotations

from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    note_path = Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "review_note.md"
    text = note_path.read_text(encoding="utf-8")

    require(
        "Pending reviewer signoff." not in text,
        "review note still contains the draft placeholder",
    )
    require("signoff" in text.lower(), "review note must mention signoff")
    print("review note verification passed")


if __name__ == "__main__":
    main()
