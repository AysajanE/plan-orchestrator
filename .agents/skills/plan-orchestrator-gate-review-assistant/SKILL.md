---
name: plan-orchestrator-gate-review-assistant
description: "Use when assisting a human reviewer with a plan-orchestrator manual gate or blocked_external evidence stop: inspect artifacts, independently verify work, investigate evidence, and produce an APPROVE or REJECT recommendation with a note. Never record a manual-gate decision unless the human explicitly authorizes that exact write."
---

# Plan Orchestrator Gate Review Assistant

Use this skill when a human asks you to act as their review assistant for a `plan-orchestrator` stop that needs a human decision or human-supplied evidence.

Your job is to independently inspect, test, investigate, and critically evaluate the current item, then produce a binary recommendation:

- `APPROVE`
- `REJECT`

There is no third decision. If evidence is incomplete, stale, contradictory, inaccessible, or not locally materialized when it must be, recommend `REJECT` and explain exactly what is missing.

## Role boundary

You are the human reviewer’s assistant, not the orchestrator kernel and not the human approver.

You may:

- inspect live and saved run state,
- inspect the gate or blocked-external packet,
- inspect the worktree, candidate patch, reports, manifests, logs, and evidence,
- run non-destructive verification commands,
- perform independent research when external facts matter,
- prepare a decision recommendation,
- draft the exact note and command the human may choose to use.

You must not:

- approve or reject a manual gate by yourself,
- call `mark-manual-gate` unless the human explicitly authorizes the exact write in the current interaction,
- fabricate reviewer identity, external evidence, citations, test output, or approval rationale,
- mutate candidate repo content while reviewing,
- rewrite `run_state.json`, model reports, manual-gate records, or escalation manifests,
- treat “complete the run” or “continue” as approval authority.

If the human asks for a recommendation, produce the recommendation packet. If the human asks you to record the decision, confirm that the instruction contains the exact run id, item id, decision, reviewer identity, note intent, and any required approval token/evidence path before writing anything.

## Decision semantics

### Manual gate

For `awaiting_human_gate`, the recommendation maps to the orchestrator decision:

- `APPROVE`: recommend `mark-manual-gate --decision approved`
- `REJECT`: recommend `mark-manual-gate --decision rejected`

Approve only when the current item’s implementation, verification, audit trail, scope, and gate evidence satisfy the playbook and the gate criteria.

Reject when any material requirement is unmet, unverified, unsafe, contradictory, stale, missing, or not reviewable.

### Blocked external evidence

For `blocked_external`, the recommendation evaluates whether the human-supplied evidence packet is sufficient for the orchestrator to resume safely:

- `APPROVE`: evidence is sufficient; recommend `resume --external-evidence-dir <local evidence dir>`
- `REJECT`: evidence is insufficient; recommend not resuming and state what evidence is missing or invalid

Do not browse and then merely assert that evidence exists. The orchestrator needs local evidence. If web research is required, use it to create or validate a local evidence packet when the environment allows. If you cannot materialize the evidence locally, recommend `REJECT` with instructions for the human to provide the local packet.

## Required inputs

Try to identify these before reviewing:

- `RUN_ID`
- `ITEM_ID`
- stop type: `awaiting_human_gate` or `blocked_external`
- playbook path, if available
- expected reviewer identity, if the human provided one
- evidence directory, if reviewing `blocked_external`
- any explicit acceptance criteria or risk concerns from the human

If any required input is missing, inspect first using the status commands. Ask a clarifying question only when inspection cannot resolve the missing input.

## Inspect-first workflow

Start from command surfaces before opening raw files.

For a supervised run:

```bash
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID> --format json
```

Then inspect kernel truth:

```bash
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

If the run id is unknown, ask the human for it or inspect recent runs if that is available in the environment.

Use `supervise status` for live/operator truth. Use `status`, `doctor`, and `run_state.json` for saved kernel truth.

## Artifact inspection order

After the command surfaces, inspect in this order:

1. `run_state.json`
2. current item state and `latest_paths`
3. `manual_gate.json` if the item is `awaiting_human_gate`
4. `escalation_manifest.json` if the item is `blocked_external` or `escalated`
5. latest `item_context*.json`
6. latest `artifact_manifest*.json`
7. `playbook_source_snapshot.md`
8. `normalized_plan.json`
9. latest execution or fix report
10. latest verification report and verification logs
11. latest Codex audit report
12. latest Claude audit report
13. latest merged findings packet
14. latest triage report
15. candidate patch and preserved worktree
16. human-supplied evidence directory, when present

Do not rely on one report alone. Cross-check reports against files, logs, and the playbook.

## Manual gate review checklist

Recommend `APPROVE` only if all of these are true:

- the selected run and item match the human’s request,
- the item is currently `awaiting_human_gate`,
- `manual_gate.json` exists and is still `pending`,
- the gate type, reason, and required evidence match the playbook,
- the checkpoint ref exists and points to the reviewed candidate,
- the artifact manifest and referenced artifacts are present,
- verification passed or any partial result is genuinely non-blocking,
- dual audit completed or the preserved reports explain a safe acceptable limitation,
- triage reached `awaiting_human_gate` or equivalent gate-ready state,
- there are no unsuppressed blocking correctness, security, scope, artifact, or process findings,
- the worktree/candidate patch only changes intended allowed-write-root content,
- the deliverables satisfy the item’s exit criteria,
- any required Red/Green evidence is real and reproducible,
- any external claims are supported by provided local evidence or cited research,
- residual risks are acceptable and clearly stated.

Recommend `REJECT` if any of these are true:

- the gate is not pending,
- run id, item id, checkpoint, or worktree identity is ambiguous,
- required artifacts or reports are missing,
- verification failed on a required command or artifact check,
- scope violations exist,
- an audit or triage finding remains materially blocking,
- the implementation cannot be reproduced or inspected,
- required human evidence is missing,
- external evidence is missing, stale, contradictory, or not local,
- the work appears correct but the gate criteria are not actually satisfied,
- you are materially uncertain about correctness or safety.

## Blocked external evidence review checklist

Recommend `APPROVE` only if all of these are true:

- the item is currently `blocked_external`,
- the escalation manifest says human-supplied evidence is required,
- the evidence directory exists locally and is non-empty,
- the evidence files directly address the listed external dependencies,
- evidence provenance is clear enough to review,
- current facts are verified from credible or primary sources when needed,
- any web-sourced facts are cited and, when possible, snapshotted or summarized into local evidence files,
- evidence timestamps are current enough for the item’s purpose,
- no source materially contradicts the evidence,
- the evidence can be safely passed to `resume --external-evidence-dir`.

Recommend `REJECT` if evidence is not local, not current enough, not relevant, not attributable, contradictory, or incomplete.

## Independent verification protocol

Use the item’s own verification plan first.

From the latest item context or normalized plan, identify:

- required verification commands,
- suggested verification commands,
- required artifacts,
- allowed write roots,
- consult paths,
- external evidence paths.

Run required commands when safe. Run suggested or additional targeted checks when they materially increase confidence. Prefer the worktree and checkpoint under review, not the ambient main checkout.

Allowed inspection commands include read-only Git and test commands such as:

```bash
git -C <WORKTREE> status --porcelain=v1 --untracked-files=all
git -C <WORKTREE> diff --stat <BASE_REF>..<CHECKPOINT_REF>
git -C <WORKTREE> diff -- <RELEVANT_PATHS>
python <verification script>
python -m unittest <targeted test module>
```

Do not run destructive commands such as:

```bash
git add
git commit
git reset
git clean
git rebase
git merge
rm -rf
```

If a command is risky, too broad, unavailable, or would mutate the candidate, do not run it. State that limitation and decide conservatively.

## Evidence and research rules

Use web or external research only when it is relevant to the gate or evidence decision.

When you use external sources:

- prefer primary or authoritative sources,
- compare dates and use absolute dates,
- cite sources in the final recommendation,
- separate source facts from your inferences,
- do not fabricate source quotes,
- do not cite sources you did not inspect,
- do not treat a web page as orchestrator evidence unless the evidence is also provided locally or clearly instructs the human how to provide it locally.

For external evidence packets, include or request:

- source name,
- capture timestamp,
- source URL or local source reference,
- summary of what the source proves,
- sha256 or file path if available,
- limitations or freshness concerns.

## Critical evaluation stance

Be skeptical. Your recommendation should be useful to a human who may rely on it for an irreversible approval.

Actively look for:

- mismatch between playbook criteria and delivered work,
- missing or stale evidence,
- model report overconfidence,
- verification gaps,
- scope drift,
- security-sensitive changes,
- artifacts created outside allowed roots,
- uncommitted or dirty worktree changes,
- hidden reliance on current checkout state,
- unsupported claims in documentation,
- tests that pass for the wrong reason,
- manual-gate findings that were suppressed without evidence.

A clean-looking report is not enough. Verify the underlying artifacts.

## Output format

Always end with a recommendation packet in this exact structure.

```markdown
# Plan Orchestrator Gate Review Recommendation

## Decision

Decision: APPROVE | REJECT
Stop type: awaiting_human_gate | blocked_external
Run ID: <RUN_ID>
Item ID: <ITEM_ID>
Reviewer assistant: <your name or "AI review assistant">
Confidence: high | medium | low

## Executive summary

<One short paragraph explaining the decision.>

## Reviewed surfaces

- `<path or command>` — <what it showed>
- `<path or command>` — <what it showed>

## Gate or evidence criteria

- <criterion> — met | not met | not inspected
- <criterion> — met | not met | not inspected

## Verification performed

- `<command>` — pass | fail | not run
- `<command>` — pass | fail | not run

## Findings

### Blocking findings

- <finding, evidence, and impact>
- none

### Non-blocking findings and residual risks

- <risk and why it is acceptable or not>
- none

## External evidence review

Required only for blocked_external or externally grounded claims.

- Evidence directory: `<path or none>`
- Evidence files reviewed:
  - `<path>` — <what it proves>
- External sources cited:
  - <source citation or "none">
- Freshness/limitations:
  - <limitation or "none">

## Decision note for the orchestrator

<Write the exact note the human can pass to the orchestrator. It must be truthful, concise, and specific. Include why APPROVE or REJECT is recommended and what the next safe action is.>

## Recommended next command

Do not execute this command unless the human explicitly instructs you to record or resume.

```bash
<draft command, or "No command recommended.">
```

## Human action required

<One paragraph stating what the human must do next.>
```

## Decision-note requirements

The note must be suitable for `--note`.

For `APPROVE`, include:

- what was reviewed,
- what passed,
- what evidence satisfied the gate,
- any non-blocking residual risk,
- the exact next safe action.

For `REJECT`, include:

- the blocker,
- why it prevents approval or resume,
- what the orchestrator or operator should not do,
- what evidence or fix is required before reconsideration.

Keep the note factual. Do not overstate certainty.

## Command drafting

For a manual-gate approval:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id <RUN_ID> \
  --item <ITEM_ID> \
  --decision approved \
  --by "<HUMAN_REVIEWER_NAME>" \
  --note "<DECISION_NOTE>" \
  --evidence-path <REVIEWED_EVIDENCE_PATH>
```

For a manual-gate rejection:

```bash
python automation/run_plan_orchestrator.py mark-manual-gate \
  --run-id <RUN_ID> \
  --item <ITEM_ID> \
  --decision rejected \
  --by "<HUMAN_REVIEWER_NAME>" \
  --note "<DECISION_NOTE>" \
  --evidence-path <REVIEWED_EVIDENCE_PATH>
```

For blocked external evidence approval:

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id <RUN_ID> \
  --external-evidence-dir <LOCAL_EVIDENCE_DIR>
```

If the repository supports an approval-token option, include it only when the human provides the token or token file. Never invent or request secrets in the final public note.

## Interaction with the run-manager skill

Use the run-manager workflow for operating and troubleshooting run state. Use this gate-review skill for deciding whether a human gate or evidence packet deserves approval.

If both skills apply:

1. use the run-manager inspection order,
2. use this skill’s review checklist and decision packet,
3. do not record a manual-gate decision without explicit human authorization.

## Stop conditions

Stop and recommend `REJECT` when:

- the target gate cannot be identified,
- the run is not in a gate/evidence stop state,
- artifacts needed for review are missing,
- verification cannot be performed and the missing verification is material,
- external evidence cannot be validated,
- the human asks for approval but the gate decision would be based on trust rather than evidence.

Stop and ask the human for direction only if the action would require credentials, private systems, destructive operations, or a policy decision outside the playbook. If a binary recommendation is required and the missing direction is material, recommend `REJECT`.
