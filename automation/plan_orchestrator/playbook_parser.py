from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .validators import compute_sha256, dedupe_preserve_order, slugify


HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
# Accept both:
# - "3.2 API Update"
# - "3.2. API Update"
# - "3. Phase Details"
NUMBERED_TITLE_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:\.)?\s+(.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")
PATH_TOKEN_RE = re.compile(
    r"(?<![\w./-])((?:[A-Za-z0-9._-]+/)+(?:[A-Za-z0-9._-]+(?:\.[A-Za-z0-9._-]+)+|[A-Za-z0-9._-]+/))"
)
FENCED_CODE_RE = re.compile(r"```(?:[a-zA-Z0-9_-]+)?\n(.*?)```", re.DOTALL)


def _split_numbered_title(raw_title: str) -> tuple[str | None, str]:
    match = NUMBERED_TITLE_RE.match(raw_title.strip())
    if not match:
        return None, raw_title.strip()
    return match.group(1), match.group(2).strip()


def _parse_pipe_row(raw_line: str) -> list[str]:
    return [cell.strip() for cell in raw_line.strip().strip("|").split("|")]


def extract_repo_paths_from_text(text: str) -> list[str]:
    found: list[str] = []
    for value in BACKTICK_PATH_RE.findall(text):
        if "/" in value:
            found.append(value.strip())
    for match in PATH_TOKEN_RE.findall(text):
        if "/" in match:
            found.append(match.strip())
    cleaned = [item.rstrip(".,;") for item in found if item.strip().lower() != "none"]
    return dedupe_preserve_order(cleaned)


def extract_code_blocks(markdown: str) -> list[str]:
    return [block.strip() for block in FENCED_CODE_RE.findall(markdown)]


def _collect_sections(lines: list[str], level: int, start_index: int, end_index: int) -> list[dict[str, Any]]:
    marker = "#" * level + " "
    headings: list[tuple[int, str]] = []
    for idx in range(start_index, end_index):
        line = lines[idx]
        if line.startswith(marker):
            headings.append((idx, line[len(marker) :].strip()))

    sections: list[dict[str, Any]] = []
    for index, (line_index, raw_title) in enumerate(headings):
        body_start = line_index + 1
        body_end = headings[index + 1][0] if index + 1 < len(headings) else end_index
        number, clean_title = _split_numbered_title(raw_title)
        body = "\n".join(lines[body_start:body_end]).strip("\n")
        sections.append(
            {
                "level": level,
                "number": number,
                "raw_heading": raw_title,
                "title": clean_title,
                "slug": slugify(clean_title),
                "line_start": line_index + 1,
                "line_end": body_end,
                "body_markdown": body,
                "subsections": _collect_sections(lines, level + 1, body_start, body_end)
                if level == 2
                else [],
            }
        )
    return sections


def _extract_ordered_execution_rows(section: dict[str, Any], lines: list[str]) -> list[dict[str, Any]]:
    section_start = section["line_start"] - 1
    section_end = section["line_end"]
    header_index: int | None = None
    headers: list[str] = []

    for idx in range(section_start, section_end):
        line = lines[idx].rstrip()
        if not line.strip().startswith("|"):
            continue
        parsed = _parse_pipe_row(line)
        normalized = [cell.strip().lower() for cell in parsed]
        if normalized and normalized[0] == "step_id":
            header_index = idx
            headers = normalized
            break

    if header_index is None:
        raise ValueError("Could not find Ordered Execution Plan table header")

    rows: list[dict[str, Any]] = []
    for idx in range(header_index + 1, section_end):
        line = lines[idx].rstrip()
        if not line.strip().startswith("|"):
            if rows:
                break
            continue
        if TABLE_SEPARATOR_RE.match(line):
            continue
        cells = _parse_pipe_row(line)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        row = dict(zip(headers, cells[: len(headers)]))
        row["source_row"] = {
            "section_title": section["title"],
            "row_index": len(rows) + 1,
            "line_start": idx + 1,
            "line_end": idx + 1,
            "raw_row_markdown": line,
        }
        rows.append(row)

    if not rows:
        raise ValueError("Ordered Execution Plan table contains no rows")
    return rows


def parse_playbook(playbook_path: str | Path) -> dict[str, Any]:
    path = Path(playbook_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    sections = _collect_sections(lines, 2, 0, len(lines))

    by_number = {section["number"]: section for section in sections if section["number"]}
    section_two = by_number.get("2")
    if section_two is None:
        raise ValueError("Playbook is missing section 2")

    ordered_execution_rows = _extract_ordered_execution_rows(section_two, lines)

    return {
        "source_path": path.as_posix(),
        "sha256": compute_sha256(path),
        "title": path.name,
        "raw_markdown": text,
        "sections": sections,
        "ordered_execution_rows": ordered_execution_rows,
    }


def section_by_number(parsed: dict[str, Any], number: str) -> dict[str, Any]:
    for section in parsed["sections"]:
        if section.get("number") == number:
            return section
    raise KeyError(f"Missing section {number}")


def subsection_map(section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        subsection["slug"]: subsection
        for subsection in section.get("subsections", [])
    }
