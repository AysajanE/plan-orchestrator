from __future__ import annotations


def normalize_status_label(value: str) -> str:
    text = value.strip()
    if not text:
        return "unknown"
    return text.lower()
