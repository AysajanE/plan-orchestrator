from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import RunState
from .validators import compute_sha256, load_json, resolve_repo_path, validate_named_schema


RUNTIME_POLICY_CHECK_KEYS = (
    "runtime_policy_path_exists",
    "runtime_policy_sha256_matches",
    "runtime_policy_matches_run_state",
)


def runtime_policy_integrity(
    repo_root: Path,
    run_state: RunState,
) -> tuple[dict[str, bool | None], dict[str, Any]]:
    checks: dict[str, bool | None] = {
        "runtime_policy_path_exists": None,
        "runtime_policy_sha256_matches": None,
        "runtime_policy_matches_run_state": None,
    }
    details: dict[str, Any] = {}

    if not run_state.runtime_policy_path:
        return checks, details

    runtime_policy_path = resolve_repo_path(repo_root, run_state.runtime_policy_path)
    details["runtime_policy_path"] = run_state.runtime_policy_path
    checks["runtime_policy_path_exists"] = runtime_policy_path.exists()
    if not runtime_policy_path.exists():
        return checks, details

    if run_state.runtime_policy_sha256:
        checks["runtime_policy_sha256_matches"] = (
            compute_sha256(runtime_policy_path) == run_state.runtime_policy_sha256
        )

    try:
        payload = load_json(runtime_policy_path)
        validate_named_schema("runtime_policy.schema.json", payload)
    except Exception as exc:
        checks["runtime_policy_matches_run_state"] = False
        details["runtime_policy_error"] = str(exc)
        return checks, details

    options_match = payload.get("options") == run_state.options.to_dict()
    sources_match = True
    if run_state.runtime_policy_sources is not None:
        sources_match = payload.get("sources") == run_state.runtime_policy_sources
    checks["runtime_policy_matches_run_state"] = options_match and sources_match
    return checks, details
