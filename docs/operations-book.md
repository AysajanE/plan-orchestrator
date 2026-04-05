# Operations Book

This runbook is for teams deploying `plan-orchestrator` against real task packs and playbooks with live AI operators.

Use it together with:

- `docs/operator-guide.md` for command and artifact details
- `docs/supervision-guide.md` for the supervision contract
- `docs/release-checklist.md` for rollout preflight and closeout checks

## 1. Purpose

This document exists to prevent a specific failure mode:

- the worker agent is told to "complete" a run,
- the run reaches `awaiting_human_gate`,
- the same agent calls `mark-manual-gate`,
- the audit trail now says `human`, but no real human approval actually occurred.

The kernel and supervisor treat `awaiting_human_gate` as the only human-only stop.
Today, that boundary is operationally enforced by team procedure and agent instructions.
It is not a strong authentication boundary.

Treat `mark-manual-gate` as a privileged operational command.

## 2. Non-Negotiable Rules

- Never treat "complete the run" as permission for an agent to approve a manual gate.
- Never let the same autonomous agent both produce the candidate work and record the human gate approval.
- Never treat a free-form `--by "Reviewer Name"` string as proof that a real human approved the gate.
- Always stop and hand control back to a designated human operator when the run reaches `awaiting_human_gate`.
- Only resume under supervision after the human approval or rejection is already recorded.

## 3. Roles

### Human operator

The human operator owns:

- choosing the playbook and launch mode
- deciding what the agent is allowed to do
- observing `supervise status`, `status`, and `doctor`
- reviewing the gate packet
- running `mark-manual-gate`, or explicitly directing another trusted human-held terminal to do it
- deciding whether a run remains trustworthy after an incident

### Worker agent

The worker agent may:

- start a supervised run
- inspect live and snapshot status
- inspect run artifacts
- wait at `awaiting_human_gate`
- resume a run after a human approval is already recorded
- resume `blocked_external` only when valid local evidence is supplied

The worker agent must not:

- approve or reject a manual gate
- call `mark-manual-gate` unless the human explicitly instructs the exact decision in that moment
- treat generic completion language as approval authority

### Supervisor

The supervisor may:

- prove fresh live attachment
- record waiting truthfully
- diagnose and repair deterministic local orchestrator drift
- resume recoverable saved stops that stay inside existing kernel semantics

The supervisor must not:

- write the manual-gate decision
- fabricate evidence
- widen the kernel state machine

## 4. Safe Instruction Patterns

Use explicit stop conditions when briefing the worker agent.

### Safe launch wording

Use prompts like:

- "Start this playbook under supervision and stop at the first `awaiting_human_gate`. Do not call `mark-manual-gate`."
- "Run until the first truthful wait or terminal stop, inspect `supervise status`, `status`, `doctor`, and the gate artifact, then wait for me."
- "Resume this run under supervision after my recorded approval only. Do not approve anything yourself."
- "If you hit `blocked_external`, stop, show me the evidence requirements, and wait for my direction."

### Unsafe wording

Do not use prompts like:

- "Complete the playbook."
- "Finish the run end to end."
- "Handle any approvals needed."
- "Take whatever next step is required to get to passed."
- "Drive the example to completion."

Those prompts are unsafe whenever the playbook may reach `awaiting_human_gate`.

## 5. Standard Operating Procedure

### Step 1: Preflight

Before launching:

1. confirm a clean tracked checkout
2. review ambient agent config
3. run:

```bash
python automation/run_plan_orchestrator.py doctor \
  --playbook path/to/playbook.md \
  --format json
```

4. brief the worker agent with an explicit stop condition

### Step 2: Launch under supervision

Use:

```bash
python automation/run_plan_orchestrator.py supervise run ...
```

or:

```bash
python automation/run_plan_orchestrator.py supervise resume ...
```

### Step 3: Observe the run

When the run is active, inspect in this order:

```bash
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

Use `supervise status` for live/operator truth.
Use `status` for saved kernel truth.

### Step 4: If the run reaches `awaiting_human_gate`

Required human actions:

1. inspect the latest `manual_gate.json`
2. inspect the item packet paths referenced in `run_state.json`
3. review the actual candidate work and evidence
4. decide `approved` or `rejected`
5. run `mark-manual-gate` from a human-controlled terminal
6. only then allow the agent or supervisor to continue

The worker agent may help gather context, but it must stop before the write action.

### Step 5: If the run reaches `blocked_external`

Required human actions:

1. inspect `status` and `doctor`
2. decide whether valid local evidence exists
3. provide `--external-evidence-dir` or an inbox path
4. let the agent resume under supervision

### Step 6: Resume only after the boundary is satisfied

After manual-gate approval:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id <RUN_ID>
```

After blocked-external evidence is present:

```bash
python automation/run_plan_orchestrator.py supervise resume \
  --run-id <RUN_ID> \
  --external-evidence-dir /absolute/path/to/evidence
```

## 6. Manual-Gate Approval Protocol

When approving a gate:

- use a human-controlled shell
- confirm the current item id and run id
- confirm the gate is still `pending`
- confirm the evidence path you cite is the one you actually reviewed
- record a meaningful `--note`

Example:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id <RUN_ID> \
  --item <ITEM_ID> \
  --decision approved \
  --by "Reviewer Name" \
  --note "Reviewed the gate packet and approve this handoff." \
  --evidence-path /absolute/path/to/reviewed/evidence
```

Do not ask the worker agent to synthesize the reviewer identity, note, or evidence path.

## 7. Two-Terminal Operating Pattern

Use two terminals during real runs:

### Terminal A: control

Use for:

- `supervise run`
- `supervise resume`
- the human-held `mark-manual-gate` command

### Terminal B: observation

Use for:

- `supervise status`
- `status`
- `doctor`
- reading `manual_gate.json`
- reading `escalation_manifest.json`
- reading supervision heartbeats and interventions

This separation makes it much easier to see whether the run actually paused before any human write action occurred.

## 8. Incident Protocol

Treat the following as an incident:

- the worker agent called `mark-manual-gate`
- the agent implied it had approval authority
- the audit trail says `human`, but no real human approved the gate
- the agent used a reviewer-looking string without an actual reviewer decision

If this happens:

1. stop issuing further resume or run commands
2. inspect `supervise status`, `status`, and `doctor`
3. inspect `manual_gate.json`
4. inspect the supervision heartbeat and intervention ledger
5. record whether a real human approved the gate
6. decide whether the run must be invalidated and restarted

Conservative rule:

- if no real human approval occurred, do not treat the saved gate approval as trustworthy just because the artifact says `human`

## 9. Example Prompts For The Team

### Start and stop at the first human gate

Use:

> Run this playbook under supervision until the first `awaiting_human_gate` stop. Do not call `mark-manual-gate`. When it stops, inspect `supervise status`, `status`, `doctor`, and `manual_gate.json`, summarize the state, and wait for my approval decision.

### Resume after the human approval is already recorded

Use:

> I have already reviewed the gate packet and recorded my decision. Resume this run under supervision and continue until the next truthful stop or terminal state.

### Handle blocked external evidence

Use:

> Resume this run under supervision using this local evidence directory. Do not browse for additional evidence and do not widen scope beyond the blocked item.

## 10. Current Limitation

Today, `mark-manual-gate` is a normal CLI command with no strong caller authentication.

That means:

- the boundary is real in workflow semantics,
- but weak in authorization,
- so the team must enforce it operationally until the runtime is hardened.

If you remember only one rule, remember this:

Never ask the worker agent to "complete" a run that may encounter `awaiting_human_gate`.
