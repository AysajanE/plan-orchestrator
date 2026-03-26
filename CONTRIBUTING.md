# Contributing

Thanks for contributing to `plan-orchestrator`.

## Development expectations

- Keep changes tightly scoped.
- Preserve the core runtime invariants documented in `README.md` and `docs/operator-guide.md`.
- Prefer stdlib-first solutions unless a dependency is clearly justified.
- Do not weaken the default local/offline-safe evidence posture.
- Keep the canonical public input contract (`markdown_playbook_v1`) coherent across code, docs, examples, and tests.

## Before opening a pull request

Run the local checks:

```bash
python3 -m unittest discover -s automation/plan_orchestrator/tests -t .
python3 automation/run_plan_orchestrator.py list-items --playbook examples/launch_demo_playbook/playbook.md
python3 automation/run_plan_orchestrator.py show-item --playbook examples/launch_demo_playbook/playbook.md --item 01 --format text
python3 automation/run_plan_orchestrator.py list-items --playbook examples/basic_markdown_playbook/playbook.md
python3 automation/run_plan_orchestrator.py show-item --playbook examples/basic_markdown_playbook/playbook.md --item 02 --format json
```

If your change affects the playbook contract, adapter normalization, or runtime behavior, add or update tests in `automation/plan_orchestrator/tests/`.

## Pull request guidance

- Explain the user-visible or operator-visible behavior change.
- Call out any contract changes explicitly.
- Include the commands you ran for verification.
- Keep unrelated refactors out of the same pull request.
