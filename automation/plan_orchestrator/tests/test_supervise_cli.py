from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from automation.plan_orchestrator import cli
from automation.plan_orchestrator.supervision_bridge import (
    KERNEL_INVOCATION_ID_ENV,
    SUPERVISION_ENABLED_ENV,
    SUPERVISOR_SESSION_ID_ENV,
)
from automation.plan_orchestrator.config import SUPERVISED_RUN_ID_OVERRIDE_ENV


@contextmanager
def _fake_context():
    yield None


class SuperviseCliTests(unittest.TestCase):
    def test_supervise_status_exit_code_contract_is_returned(self) -> None:
        payload = {
            "run_id": "RUN_SUPERVISED",
            "kernel_status": {"status_level": "ok", "exit_code": 0, "current_state": "ST40_VERIFYING"},
            "supervision_status": {
                "claim_class": "attachment_unproven",
                "exit_code": 11,
                "reason": "Fresh attachment is missing.",
            },
        }

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.build_supervision_status",
            return_value=payload,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "supervise",
                        "status",
                        "--run-id",
                        "RUN_SUPERVISED",
                        "--format",
                        "json",
                        "--exit-code",
                    ]
                )

        self.assertEqual(exit_code, 11)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_supervise_run_dispatches_to_supervisor(self) -> None:
        payload = {
            "run_id": "RUN_SUPERVISED",
            "outcome": "passed",
            "supervisor_session_id": "svs_deadbeef",
            "status": {
                "supervision_status": {"claim_class": "terminal_observed", "exit_code": 12},
                "kernel_status": {"status_level": "ok", "exit_code": 0},
            },
        }

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.supervise_run",
            return_value=payload,
        ) as fake_supervise_run:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "supervise",
                        "run",
                        "--playbook",
                        "playbook.md",
                        "--item",
                        "01",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        fake_supervise_run.assert_called_once()

    def test_supervise_resume_dispatches_to_supervisor(self) -> None:
        payload = {
            "run_id": "RUN_SUPERVISED",
            "outcome": "parked",
            "supervisor_session_id": "svs_deadbeef",
            "status": {
                "supervision_status": {"claim_class": "terminal_observed", "exit_code": 12},
                "kernel_status": {"status_level": "error", "exit_code": 2},
            },
        }

        with mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.supervise_resume",
            return_value=payload,
        ) as fake_supervise_resume:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "supervise",
                        "resume",
                        "--run-id",
                        "RUN_SUPERVISED",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        fake_supervise_resume.assert_called_once()

    def test_plain_run_bootstraps_kernel_bridge_when_supervision_env_is_present(self) -> None:
        fake_orchestrator = mock.Mock()
        fake_orchestrator.run_new.return_value = {
            "run_id": "RUN_SUPERVISED",
            "current_state": "ST05_PLAN_NORMALIZED",
            "current_item_id": "01",
            "last_terminal_state": "passed",
            "run_state_path": ".local/automation/plan_orchestrator/runs/RUN_SUPERVISED/run_state.json",
        }

        seen = {}

        def fake_bridge(repo_root: Path, run_id: str):
            seen["repo_root"] = repo_root
            seen["run_id"] = run_id
            return _fake_context()

        env = {
            SUPERVISION_ENABLED_ENV: "1",
            SUPERVISOR_SESSION_ID_ENV: "svs_deadbeef",
            KERNEL_INVOCATION_ID_ENV: "kernel_deadbeef",
            SUPERVISED_RUN_ID_OVERRIDE_ENV: "RUN_SUPERVISED",
        }

        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "automation.plan_orchestrator.cli.resolve_repo_root",
            return_value=Path("."),
        ), mock.patch(
            "automation.plan_orchestrator.cli.PlanOrchestrator",
            return_value=fake_orchestrator,
        ), mock.patch(
            "automation.plan_orchestrator.cli.kernel_supervision_bridge",
            side_effect=fake_bridge,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "run",
                        "--playbook",
                        "playbook.md",
                        "--item",
                        "01",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(seen["run_id"], "RUN_SUPERVISED")
        self.assertEqual(json.loads(stdout.getvalue())["run_id"], "RUN_SUPERVISED")
        self.assertEqual(stderr.getvalue(), "")
