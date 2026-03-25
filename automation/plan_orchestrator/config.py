from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .models import RuntimeOptions

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


def make_run_id(now: datetime | None = None) -> str:
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
    auto_advance: bool,
    max_items: Optional[int],
) -> RuntimeOptions:
    return RuntimeOptions(
        codex_model=_env_str("PLAN_ORCHESTRATOR_CODEX_MODEL", DEFAULT_CODEX_MODEL),
        codex_reasoning_effort=_env_str(
            "PLAN_ORCHESTRATOR_CODEX_REASONING_EFFORT",
            DEFAULT_CODEX_REASONING_EFFORT,
        ),
        claude_model=_env_str("PLAN_ORCHESTRATOR_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL),
        claude_effort=_env_str("PLAN_ORCHESTRATOR_CLAUDE_EFFORT", DEFAULT_CLAUDE_EFFORT),
        auto_advance=auto_advance,
        max_items=max_items,
        max_fix_rounds=_env_int("PLAN_ORCHESTRATOR_MAX_FIX_ROUNDS", DEFAULT_MAX_FIX_ROUNDS),
        max_remediation_rounds=_env_int(
            "PLAN_ORCHESTRATOR_MAX_REMEDIATION_ROUNDS",
            DEFAULT_MAX_REMEDIATION_ROUNDS,
        ),
        execution_timeout_sec=_env_int(
            "PLAN_ORCHESTRATOR_EXECUTION_TIMEOUT_SEC",
            DEFAULT_EXECUTION_TIMEOUT_SEC,
        ),
        verification_timeout_sec=_env_int(
            "PLAN_ORCHESTRATOR_VERIFICATION_TIMEOUT_SEC",
            DEFAULT_VERIFICATION_TIMEOUT_SEC,
        ),
        audit_timeout_sec=_env_int(
            "PLAN_ORCHESTRATOR_AUDIT_TIMEOUT_SEC",
            DEFAULT_AUDIT_TIMEOUT_SEC,
        ),
        triage_timeout_sec=_env_int(
            "PLAN_ORCHESTRATOR_TRIAGE_TIMEOUT_SEC",
            DEFAULT_TRIAGE_TIMEOUT_SEC,
        ),
        fix_timeout_sec=_env_int("PLAN_ORCHESTRATOR_FIX_TIMEOUT_SEC", DEFAULT_FIX_TIMEOUT_SEC),
        remediation_timeout_sec=_env_int(
            "PLAN_ORCHESTRATOR_REMEDIATION_TIMEOUT_SEC",
            DEFAULT_REMEDIATION_TIMEOUT_SEC,
        ),
    )


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
