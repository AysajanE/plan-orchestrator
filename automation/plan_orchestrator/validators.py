from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class ValidationError(ValueError):
    """Raised when a JSON document fails schema validation."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data: Any) -> None:
    ensure_directory(path.parent)
    payload = json.dumps(data, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=path.name + ".tmp.",
    ) as handle:
        handle.write(payload)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_path_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_file():
        return compute_sha256(path)
    if not path.is_dir():
        return None

    h = hashlib.sha256()
    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file_path.relative_to(path).as_posix().encode("utf-8")
        h.update(rel)
        h.update(b"\0")
        h.update(compute_sha256(file_path).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def repo_relative_path(repo_root: Path, path_or_str: str | Path) -> str:
    path = Path(path_or_str)
    if not path.is_absolute():
        return PurePosixPath(path.as_posix()).as_posix()
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_repo_path(repo_root: Path, path_or_str: str | Path) -> Path:
    path = Path(path_or_str)
    return path if path.is_absolute() else repo_root / path


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def _pure(path: str) -> PurePosixPath:
    return PurePosixPath(path.strip("/"))


def relative_path_is_within(rel_path: str, root: str) -> bool:
    rel = _pure(rel_path)
    base = _pure(root)
    return rel == base or base in rel.parents


def out_of_scope_paths(
    paths: list[str],
    allowed_roots: list[str],
    ignored_roots: list[str] | None = None,
) -> list[str]:
    allowed = [root.strip("/") for root in allowed_roots if root.strip("/")]
    ignored = [root.strip("/") for root in (ignored_roots or []) if root.strip("/")]
    if not allowed:
        return paths[:]

    bad: list[str] = []
    for rel_path in paths:
        rel_path = rel_path.strip("/")
        if any(relative_path_is_within(rel_path, root) for root in ignored):
            continue
        if not any(relative_path_is_within(rel_path, root) for root in allowed):
            bad.append(rel_path)
    return bad


def _schema_dir(default_root: Path | None = None) -> Path:
    if default_root is not None:
        return default_root
    return Path(__file__).resolve().parent / "schemas"


def load_schema(schema_name_or_path: str | Path, schema_dir: Path | None = None) -> dict[str, Any]:
    candidate = Path(schema_name_or_path)
    if candidate.exists():
        return load_json(candidate)
    schema_path = _schema_dir(schema_dir) / str(schema_name_or_path)
    return load_json(schema_path)


def validate_json_file(
    schema_name_or_path: str | Path,
    json_path: Path,
    schema_dir: Path | None = None,
) -> dict[str, Any]:
    data = load_json(json_path)
    validate_named_schema(schema_name_or_path, data, schema_dir=schema_dir)
    return data


def validate_named_schema(
    schema_name_or_path: str | Path,
    data: Any,
    schema_dir: Path | None = None,
) -> None:
    schema = load_schema(schema_name_or_path, schema_dir=schema_dir)
    validate_data_against_schema(data, schema)


def validate_data_against_schema(data: Any, schema: dict[str, Any]) -> None:
    _validate_schema_instance(data, schema, schema, "$")


def _resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValidationError(f"Unsupported $ref: {ref}")
    node: Any = root_schema
    for part in ref[2:].split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise ValidationError(f"Broken $ref: {ref}")
    if not isinstance(node, dict):
        raise ValidationError(f"Resolved $ref is not an object: {ref}")
    return node


def _matches_subschema(value: Any, schema: dict[str, Any], root_schema: dict[str, Any]) -> bool:
    try:
        _validate_schema_instance(value, schema, root_schema, "$match")
        return True
    except ValidationError:
        return False


def _validate_type(value: Any, expected_type: str, path: str) -> None:
    ok = False
    if expected_type == "object":
        ok = isinstance(value, dict)
    elif expected_type == "array":
        ok = isinstance(value, list)
    elif expected_type == "string":
        ok = isinstance(value, str)
    elif expected_type == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected_type == "boolean":
        ok = isinstance(value, bool)
    elif expected_type == "null":
        ok = value is None
    elif expected_type == "number":
        ok = (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    else:
        raise ValidationError(f"{path}: unsupported schema type {expected_type!r}")

    if not ok:
        raise ValidationError(f"{path}: expected type {expected_type}")


def _validate_datetime_string(value: str, path: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{path}: invalid date-time format") from exc


def _validate_schema_instance(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
) -> None:
    if "$ref" in schema:
        resolved = _resolve_ref(root_schema, schema["$ref"])
        _validate_schema_instance(value, resolved, root_schema, path)
        return

    if "allOf" in schema:
        for subschema in schema["allOf"]:
            if not isinstance(subschema, dict):
                raise ValidationError(f"{path}: allOf member must be an object")
            _validate_schema_instance(value, subschema, root_schema, path)

    if "if" in schema:
        if _matches_subschema(value, schema["if"], root_schema):
            then_schema = schema.get("then")
            if isinstance(then_schema, dict):
                _validate_schema_instance(value, then_schema, root_schema, path)
        else:
            else_schema = schema.get("else")
            if isinstance(else_schema, dict):
                _validate_schema_instance(value, else_schema, root_schema, path)

    if "const" in schema and value != schema["const"]:
        raise ValidationError(f"{path}: expected const {schema['const']!r}")

    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError(f"{path}: value {value!r} is not in enum")

    if "type" in schema:
        expected = schema["type"]
        if isinstance(expected, list):
            matched = False
            last_error: ValidationError | None = None
            for one in expected:
                try:
                    _validate_type(value, one, path)
                    matched = True
                    break
                except ValidationError as exc:
                    last_error = exc
            if not matched:
                raise last_error or ValidationError(f"{path}: invalid type")
        else:
            _validate_type(value, expected, path)

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ValidationError(f"{path}: string shorter than minLength")
        if "pattern" in schema and not re.match(str(schema["pattern"]), value):
            raise ValidationError(f"{path}: string does not match required pattern")
        if schema.get("format") == "date-time":
            _validate_datetime_string(value, path)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValidationError(f"{path}: number below minimum")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValidationError(f"{path}: array shorter than minItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_instance(item, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ValidationError(f"{path}: missing required key {key!r}")
        if schema.get("additionalProperties") is False:
            extras = set(value.keys()) - set(props.keys())
            if extras:
                extra_list = ", ".join(sorted(extras))
                raise ValidationError(f"{path}: unexpected keys: {extra_list}")
        for key, subschema in props.items():
            if key in value and isinstance(subschema, dict):
                _validate_schema_instance(value[key], subschema, root_schema, f"{path}.{key}")
