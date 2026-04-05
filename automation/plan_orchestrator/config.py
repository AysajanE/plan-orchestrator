from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .models import RuntimeOptions
from .validators import resolve_repo_path, utc_now_iso, validate_named_schema

# Intentional public v1 packaging choice:
# keep the runtime under automation/plan_orchestrator/ and the launcher at
# automation/run_plan_orchestrator.py so the repo stays checkout-runnable
# without an installation step and prompt/schema asset paths stay stable.

DEFAULT_PLAYBOOK_PATH_ENV = "PLAN_ORCHESTRATOR_PLAYBOOK_PATH"

DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CODEX_REASONING_EFFORT = "xhigh"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_EFFORT = "max"

DEFAULT_EXECUTION_TIMEOUT_SEC = 7200
DEFAULT_VERIFICATION_TIMEOUT_SEC = 3600
DEFAULT_AUDIT_TIMEOUT_SEC = 3600
DEFAULT_TRIAGE_TIMEOUT_SEC = 1800
DEFAULT_FIX_TIMEOUT_SEC = 5400
DEFAULT_REMEDIATION_TIMEOUT_SEC = 5400
DEFAULT_MAX_FIX_ROUNDS = 2
DEFAULT_MAX_REMEDIATION_ROUNDS = 1
CLAUDE_MAX_TURNS_DEFAULT = 8

RUNS_ROOT = Path(".local/automation/plan_orchestrator/runs")
REPORTS_ROOT = Path(".local/ai/plan_orchestrator/runs")
WORKTREES_ROOT = Path(".local/automation/plan_orchestrator/worktrees")
PROMPTS_ROOT = Path("automation/plan_orchestrator/prompts")
SCHEMAS_ROOT = Path("automation/plan_orchestrator/schemas")

CLEAN_ENV_CONFIRM_ENV = "PLAN_ORCHESTRATOR_CLEAN_ENV_CONFIRMED"
DEFAULT_CONTROL_PLANE_PATH = Path("plan_orchestrator.json")

# Internal-only supervisory seam:
# the parent supervisor preallocates a run id and passes it to the child kernel
# invocation through this env var. Plain unsupervised runs do not use it.
SUPERVISED_RUN_ID_OVERRIDE_ENV = "PLAN_ORCHESTRATOR_RUN_ID_OVERRIDE"

RUNTIME_POLICY_FIELD_NAMES = tuple(RuntimeOptions.__dataclass_fields__.keys())
RUNTIME_POLICY_SOURCE_VALUES = ("default", "repo_config", "config_file", "env", "cli")

ENV_RUNTIME_POLICY_FIELDS = {
    "PLAN_ORCHESTRATOR_CODEX_MODEL": ("codex_model", str),
    "PLAN_ORCHESTRATOR_CODEX_REASONING_EFFORT": ("codex_reasoning_effort", str),
    "PLAN_ORCHESTRATOR_CLAUDE_MODEL": ("claude_model", str),
    "PLAN_ORCHESTRATOR_CLAUDE_EFFORT": ("claude_effort", str),
    "PLAN_ORCHESTRATOR_MAX_FIX_ROUNDS": ("max_fix_rounds", int),
    "PLAN_ORCHESTRATOR_MAX_REMEDIATION_ROUNDS": ("max_remediation_rounds", int),
    "PLAN_ORCHESTRATOR_EXECUTION_TIMEOUT_SEC": ("execution_timeout_sec", int),
    "PLAN_ORCHESTRATOR_VERIFICATION_TIMEOUT_SEC": ("verification_timeout_sec", int),
    "PLAN_ORCHESTRATOR_AUDIT_TIMEOUT_SEC": ("audit_timeout_sec", int),
    "PLAN_ORCHESTRATOR_TRIAGE_TIMEOUT_SEC": ("triage_timeout_sec", int),
    "PLAN_ORCHESTRATOR_FIX_TIMEOUT_SEC": ("fix_timeout_sec", int),
    "PLAN_ORCHESTRATOR_REMEDIATION_TIMEOUT_SEC": ("remediation_timeout_sec", int),
}


@dataclass(frozen=True)
class RuntimePolicyResolution:
    options: RuntimeOptions
    sources: dict[str, str]
    repo_control_plane_path: str | None
    overlay_control_plane_path: str | None


@dataclass(frozen=True)
class RunDirectories:
    repo_root: Path
    run_root: Path
    report_root: Path
    worktrees_root: Path

    @property
    def run_state_path(self) -> Path:
        return self.run_root / "run_state.json"

    def item_control_dir(self, item_id: str, attempt_number: int) -> Path:
        return self.run_root / "items" / item_id / f"attempt-{attempt_number}"

    def item_report_dir(self, item_id: str, attempt_number: int) -> Path:
        return self.report_root / "items" / item_id / f"attempt-{attempt_number}"


def default_playbook_path() -> str | None:
    value = os.environ.get(DEFAULT_PLAYBOOK_PATH_ENV, "").strip()
    return value or None


def supervised_run_id_override() -> str | None:
    value = os.environ.get(SUPERVISED_RUN_ID_OVERRIDE_ENV, "").strip()
    return value or None


def resolve_repo_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for current in [candidate, *candidate.parents]:
        if (current / "automation" / "plan_orchestrator").is_dir() and (
            current / "automation" / "run_plan_orchestrator.py"
        ).exists():
            return current
    raise RuntimeError(
        "Could not locate the plan-orchestrator repo root from the current working directory."
    )


def resolve_run_directories(repo_root: Path, run_id: str) -> RunDirectories:
    return RunDirectories(
        repo_root=repo_root,
        run_root=repo_root / RUNS_ROOT / run_id,
        report_root=repo_root / REPORTS_ROOT / run_id,
        worktrees_root=repo_root / WORKTREES_ROOT,
    )


def make_run_id(
    now: datetime | None = None,
    *,
    allow_override: bool = True,
) -> str:
    if allow_override:
        override = supervised_run_id_override()
        if override:
            return override

    value = now or datetime.now(timezone.utc)
    return "RUN_" + value.strftime("%Y%m%dT%H%M%SZ") + f"_{uuid4().hex}"


def schema_file(repo_root: Path, filename: str) -> Path:
    return repo_root / SCHEMAS_ROOT / filename


def prompt_file(repo_root: Path, filename: str) -> Path:
    return repo_root / PROMPTS_ROOT / filename


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


def default_runtime_options(
    *,
    auto_advance: bool | None,
    max_items: Optional[int],
) -> RuntimeOptions:
    return _resolve_runtime_policy(
        repo_root=None,
        config_path=None,
        cli_auto_advance=auto_advance,
        cli_max_items=max_items,
    ).options


def resolve_runtime_policy(
    repo_root: Path,
    *,
    config_path: str | Path | None = None,
    cli_auto_advance: bool | None = None,
    cli_max_items: int | None = None,
) -> RuntimePolicyResolution:
    return _resolve_runtime_policy(
        repo_root=repo_root,
        config_path=config_path,
        cli_auto_advance=cli_auto_advance,
        cli_max_items=cli_max_items,
    )


def runtime_policy_snapshot_payload(resolution: RuntimePolicyResolution) -> dict:
    payload = {
        "schema_version": "plan_orchestrator.runtime_policy.v1",
        "resolved_at_utc": utc_now_iso(),
        "repo_control_plane_path": resolution.repo_control_plane_path,
        "overlay_control_plane_path": resolution.overlay_control_plane_path,
        "sources": dict(resolution.sources),
        "options": resolution.options.to_dict(),
    }
    validate_named_schema("runtime_policy.schema.json", payload)
    return payload


def _resolve_runtime_policy(
    *,
    repo_root: Path | None,
    config_path: str | Path | None,
    cli_auto_advance: bool | None,
    cli_max_items: int | None,
) -> RuntimePolicyResolution:
    values = _runtime_policy_defaults()
    sources = {field: "default" for field in RUNTIME_POLICY_FIELD_NAMES}

    repo_control_plane_path: str | None = None
    overlay_control_plane_path: str | None = None

    if repo_root is not None:
        repo_config = repo_root / DEFAULT_CONTROL_PLANE_PATH
        if repo_config.exists():
            repo_control_plane_path = repo_config.relative_to(repo_root).as_posix()
            _apply_policy_overrides(
                values,
                sources,
                overrides=_load_control_plane(repo_config),
                source_label="repo_config",
            )

    if config_path is not None:
        if repo_root is None:
            overlay_path = Path(config_path)
        else:
            overlay_path = resolve_repo_path(repo_root, config_path)
        overlay_control_plane_path = (
            overlay_path.relative_to(repo_root).as_posix()
            if repo_root is not None and overlay_path.is_relative_to(repo_root)
            else overlay_path.as_posix()
        )
        _apply_policy_overrides(
            values,
            sources,
            overrides=_load_control_plane(overlay_path),
            source_label="config_file",
        )

    env_overrides = _load_env_runtime_policy_overrides()
    _apply_policy_overrides(values, sources, overrides=env_overrides, source_label="env")

    cli_overrides: dict[str, object] = {}
    if cli_auto_advance is True:
        cli_overrides["auto_advance"] = True
    if cli_max_items is not None:
        cli_overrides["max_items"] = cli_max_items
    _apply_policy_overrides(values, sources, overrides=cli_overrides, source_label="cli")

    return RuntimePolicyResolution(
        options=RuntimeOptions.from_dict(values),
        sources=sources,
        repo_control_plane_path=repo_control_plane_path,
        overlay_control_plane_path=overlay_control_plane_path,
    )


def _runtime_policy_defaults() -> dict[str, object]:
    return {
        "codex_model": DEFAULT_CODEX_MODEL,
        "codex_reasoning_effort": DEFAULT_CODEX_REASONING_EFFORT,
        "claude_model": DEFAULT_CLAUDE_MODEL,
        "claude_effort": DEFAULT_CLAUDE_EFFORT,
        "auto_advance": False,
        "max_items": None,
        "max_fix_rounds": DEFAULT_MAX_FIX_ROUNDS,
        "max_remediation_rounds": DEFAULT_MAX_REMEDIATION_ROUNDS,
        "execution_timeout_sec": DEFAULT_EXECUTION_TIMEOUT_SEC,
        "verification_timeout_sec": DEFAULT_VERIFICATION_TIMEOUT_SEC,
        "audit_timeout_sec": DEFAULT_AUDIT_TIMEOUT_SEC,
        "triage_timeout_sec": DEFAULT_TRIAGE_TIMEOUT_SEC,
        "fix_timeout_sec": DEFAULT_FIX_TIMEOUT_SEC,
        "remediation_timeout_sec": DEFAULT_REMEDIATION_TIMEOUT_SEC,
    }


def _load_control_plane(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_named_schema("control_plane.schema.json", data)
    return dict(data.get("runtime_policy", {}))


def _load_env_runtime_policy_overrides() -> dict[str, object]:
    overrides: dict[str, object] = {}
    for env_name, (field_name, caster) in ENV_RUNTIME_POLICY_FIELDS.items():
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        overrides[field_name] = caster(raw)
    return overrides


def _apply_policy_overrides(
    values: dict[str, object],
    sources: dict[str, str],
    *,
    overrides: dict[str, object],
    source_label: str,
) -> None:
    for field_name, value in overrides.items():
        values[field_name] = value
        sources[field_name] = source_label


def detect_ambient_agent_configs(repo_root: Path) -> list[str]:
    candidates = [
        Path.home() / ".codex" / "config.toml",
        repo_root / ".codex" / "config.toml",
        Path.home() / ".claude" / "settings.json",
        repo_root / ".claude" / "settings.json",
        repo_root / ".mcp.json",
    ]
    return [path.as_posix() for path in candidates if path.exists()]


def assert_clean_agent_environment(repo_root: Path) -> None:
    detected = detect_ambient_agent_configs(repo_root)
    if detected and os.environ.get(CLEAN_ENV_CONFIRM_ENV) != "1":
        lines = "\n".join(f"- {path}" for path in detected)
        raise RuntimeError(
            "Ambient agent configuration was detected.\n"
            "Use a clean runner environment or dedicated service account.\n"
            f"If you have intentionally reviewed and accepted this risk, set {CLEAN_ENV_CONFIRM_ENV}=1.\n"
            f"Detected paths:\n{lines}"
        )
