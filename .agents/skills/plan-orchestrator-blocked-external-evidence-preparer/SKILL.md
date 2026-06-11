---
name: plan-orchestrator-blocked-external-evidence-preparer
description: "Prepares high-quality local evidence packets for plan-orchestrator blocked_external stops. Use when a run parks on blocked_external, needs human-supplied external evidence, outside execution, research, screenshots, API checks, or owner-assisted operations before resume."
---

# Plan Orchestrator Blocked-External Evidence Preparer

Use this skill when a `plan-orchestrator` run stops at `blocked_external` and needs a local evidence packet prepared outside the orchestrator loop.

Your job is to help the human owner produce the best, highest-quality evidence packet possible, not merely the minimum acceptable packet.

The finished packet must let the orchestrator resume with a precise local directory:

```bash
python automation/run_plan_orchestrator.py resume \
  --run-id <RUN_ID> \
  --external-evidence-dir <LOCAL_EVIDENCE_DIR>
```

Use `supervise resume` instead when live supervisory truth matters.

## Core purpose

The orchestrator must not browse, improvise, authenticate to outside services, or perform external operations inside its loop. This skill covers the outside-the-loop work:

- understand exactly what evidence is needed,
- debrief the human owner,
- collect and verify evidence with available tools,
- coordinate owner-only manual operations,
- package evidence locally,
- hash and document the packet,
- provide clear resume instructions for the orchestrator.

## Role boundary

You are the human owner’s evidence-preparation assistant.

You may:

- inspect run state, escalation manifests, item context, playbook snapshots, reports, logs, and worktrees,
- perform independent research when external facts matter,
- run outside-the-orchestrator commands and tools when safe and authorized,
- create a local evidence packet directory,
- write evidence summaries, manifests, hashes, logs, and resume instructions,
- ask the owner to perform credentialed, physical, or policy-bound operations,
- validate that the packet is ready for orchestrator resume.

You must not:

- fabricate evidence,
- imply that remote facts are available to the orchestrator unless they are captured locally,
- put secrets, tokens, cookies, private keys, or unnecessary personal data into the packet,
- mutate `run_state.json`, manual gate records, escalation manifests, model reports, or orchestrator history,
- approve or reject a manual gate,
- call `resume` or `mark-manual-gate` unless the human explicitly instructs that exact action,
- use destructive, credentialed, or production-impacting operations without explicit owner direction.

## Standard of evidence

Aim for high-assurance, reviewable evidence.

Every important claim should trace to at least one local file in the packet. Strong packets usually include:

- primary-source material where possible,
- exact capture timestamps in UTC,
- source URLs or source identifiers,
- command logs with cwd, command, timestamps, exit code, stdout, and stderr,
- screenshots or exported reports when a human-visible interface matters,
- hashes for every file,
- owner attestations for owner-only operations,
- limitations and freshness notes,
- a concise orchestrator-facing summary.

If evidence is weak, stale, contradictory, incomplete, or not locally captured, mark the packet `NOT_READY`.

## Inspect-first workflow

Start from command surfaces, not raw files.

For a supervised run:

```bash
python automation/run_plan_orchestrator.py supervise status --run-id <RUN_ID> --format json
```

Then inspect kernel truth:

```bash
python automation/run_plan_orchestrator.py status --run-id <RUN_ID> --format json
python automation/run_plan_orchestrator.py doctor --run-id <RUN_ID> --format json
```

After status and doctor, inspect:

1. current item in `run_state.json`
2. current item `latest_paths`
3. `escalation_manifest.json`
4. latest `item_context*.json`
5. latest `artifact_manifest*.json`
6. `playbook_source_snapshot.md`
7. `normalized_plan.json`
8. latest execution or fix report
9. latest verification report and logs
10. latest audit and triage reports when present
11. any existing evidence directory or inbox supplied by the owner

The escalation manifest and item context define the evidence scope. Do not infer a broader or narrower scope without explaining why.

## Owner debrief before collection

Before collecting evidence, give the owner a short debrief:

```markdown
# Blocked External Evidence Debrief

Run ID: <RUN_ID>
Item ID: <ITEM_ID>
Blocked reason: <summary from escalation manifest>
Evidence required: <specific evidence needed>
Why the orchestrator cannot collect it: <external, credentialed, manual, web, device, or policy reason>

## Best possible evidence packet

- <strongest file/source/action>
- <corroborating file/source/action>
- <owner-only action if needed>

## What I can do

- <research/check/run/capture/package tasks>

## What I need from you

- <manual login/upload/screenshot/API export/approval>

## Proposed packet directory

`<LOCAL_EVIDENCE_DIR>`
```

Proceed with collection after debrief unless the owner changes direction.

## Default packet location

Prefer a gitignored local directory unless the owner specifies another safe location:

```text
.local/operator_evidence/plan_orchestrator/<RUN_ID>/<ITEM_ID>/<YYYYMMDDTHHMMSSZ>/
```

Do not put evidence inside the orchestrator run root unless the owner explicitly asks. The evidence directory is an input packet, not a second run-state file.

## Evidence packet layout

Create this structure when possible:

```text
<LOCAL_EVIDENCE_DIR>/
  README.md
  manifest.json
  evidence_log.md
  hashes.sha256
  resume_instructions.md
  owner_actions.md
  sources/
  captures/
  commands/
  artifacts/
  redactions/
```

Use only the directories that are needed, but always include:

- `README.md`
- `manifest.json`
- `evidence_log.md`
- `hashes.sha256`
- `resume_instructions.md`

## README.md requirements

The packet README must explain:

- run id and item id,
- blocked reason,
- evidence objective,
- evidence summary,
- how each material file supports the objective,
- freshness and limitations,
- whether the packet is `READY_FOR_RESUME` or `NOT_READY`.

## manifest.json requirements

Use this shape unless a project-specific schema exists:

```json
{
  "schema_version": "plan_orchestrator.external_evidence_packet.v1",
  "run_id": "<RUN_ID>",
  "item_id": "<ITEM_ID>",
  "prepared_at_utc": "<UTC timestamp>",
  "prepared_by": "AI evidence assistant",
  "human_owner": "<owner name or unknown>",
  "packet_status": "READY_FOR_RESUME",
  "blocked_reason": "<from escalation manifest>",
  "evidence_objective": "<what this packet proves>",
  "evidence_files": [
    {
      "path": "sources/provider_status_snapshot.md",
      "sha256": "<sha256>",
      "source_type": "web_snapshot | command_log | owner_attestation | api_response | screenshot | exported_report | other",
      "captured_at_utc": "<UTC timestamp>",
      "purpose": "<what this file proves>",
      "limitations": []
    }
  ],
  "owner_actions": [
    {
      "action": "<what the owner did>",
      "performed_at_utc": "<UTC timestamp or unknown>",
      "evidence_path": "owner_actions.md"
    }
  ],
  "limitations": [],
  "recommended_resume_command": "python automation/run_plan_orchestrator.py resume --run-id <RUN_ID> --external-evidence-dir <LOCAL_EVIDENCE_DIR>"
}
```

If the packet is not ready, set `packet_status` to `NOT_READY` and explain exactly what remains missing.

## evidence_log.md requirements

Maintain a chronological log:

```markdown
# Evidence Log

## <UTC timestamp>

- Actor: AI evidence assistant | human owner
- Action: <what happened>
- Tool/source: <tool, command, browser, API, owner action>
- Output files:
  - `<relative path>`
- Result: <what was learned>
- Limitations: <anything uncertain>
```

## Owner-assisted evidence

Use `owner_actions.md` when the human performs manual or credentialed steps.

Record:

- requested action,
- who performed it,
- when it was performed,
- system/service involved,
- output file path,
- redactions or limitations.

Never ask the owner to paste secrets. Ask for redacted screenshots, exported reports, sanitized output, or an owner attestation when possible.

When owner help is needed, provide exact instructions:

```markdown
# Owner Request

Please perform this action outside the orchestrator:

1. <step>
2. <step>
3. Save or upload the result as: `<expected path or file name>`
4. Redact: <secrets or personal data to remove>
5. Include timestamp/source context: <required context>

Evidence is strong if:
- <quality criterion>

Evidence is insufficient if:
- <failure criterion>
```

After the owner provides evidence, inspect it, summarize it, hash it, and add it to the manifest.

## Command evidence protocol

When running commands, capture evidence under `commands/`.

For each command, record:

- command id,
- command line,
- cwd,
- start and end UTC timestamps,
- exit code,
- stdout file,
- stderr file,
- generated artifacts,
- limitations.

Prefer non-destructive commands. If a command may mutate systems, require explicit owner approval first.

Use this file naming pattern:

```text
commands/001_<short-name>.command.md
commands/001_<short-name>.stdout.log
commands/001_<short-name>.stderr.log
```

## Web and source evidence protocol

When external facts matter, use authoritative sources first.

For every web or external source, capture locally:

- source title or provider,
- URL or source id,
- capture timestamp in UTC,
- relevant facts in your own words,
- short compliant excerpts only when necessary,
- screenshot/export if helpful and allowed,
- limitations and freshness concerns.

Store these under `sources/` or `captures/`.

Do not tell the orchestrator “see website.” The orchestrator receives local files only.

## Redaction and sensitive data rules

Before finalizing the packet:

- remove secrets, tokens, cookies, private keys, passwords, and session identifiers,
- redact unnecessary personal data,
- preserve enough context to prove the fact,
- document redactions in `redactions/README.md` or manifest limitations,
- never alter evidence silently.

If sensitive evidence is unavoidable, flag it clearly in `README.md` and `manifest.json` and avoid pasting it in chat.

## Hashing protocol

Hash every packet file as close to collection as practical.

Regenerate `hashes.sha256` after final changes. Use Python stdlib when shell tools are not portable:

```bash
python - <<'PY'
from pathlib import Path
import hashlib

root = Path("<LOCAL_EVIDENCE_DIR>")
rows = []
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    rel = path.relative_to(root).as_posix()
    if rel == "hashes.sha256":
        continue
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    rows.append(f"{h.hexdigest()}  {rel}")
(root / "hashes.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
PY
```

Update manifest file hashes after generating the final hash list.

## Packet validation checklist

Before marking the packet ready, verify:

- the run still points to the same blocked item,
- the stop is still `blocked_external`,
- the local evidence directory exists and is non-empty,
- required packet files exist,
- every material evidence file appears in `manifest.json`,
- every material evidence file appears in `hashes.sha256`,
- the evidence directly addresses every dependency in the escalation manifest,
- owner-only actions are documented,
- web/API/command evidence has UTC timestamps,
- no obvious secrets are included,
- limitations are explicit,
- the recommended resume command uses the exact evidence directory.

If any required condition fails, mark the packet `NOT_READY`.

## Readiness states

Use only these readiness labels:

- `READY_FOR_RESUME`: packet is complete enough for the orchestrator to resume safely.
- `NOT_READY`: packet is incomplete, weak, stale, unsafe, contradictory, or missing local evidence.

Do not use “probably ready” or “maybe ready.” If confidence is not enough, use `NOT_READY`.

## resume_instructions.md requirements

Write:

```markdown
# Resume Instructions

Packet status: READY_FOR_RESUME | NOT_READY
Run ID: <RUN_ID>
Item ID: <ITEM_ID>
Evidence directory: <LOCAL_EVIDENCE_DIR>

## Recommended command

python automation/run_plan_orchestrator.py resume --run-id <RUN_ID> --external-evidence-dir <LOCAL_EVIDENCE_DIR>

## Supervised alternative

python automation/run_plan_orchestrator.py supervise resume --run-id <RUN_ID> --external-evidence-dir <LOCAL_EVIDENCE_DIR>

## Orchestrator-facing evidence summary

<Concise factual summary of what this packet proves and what files support it.>

## Limitations

<Any limitations or "none".>
```

Do not execute the resume command unless the human explicitly asks.

## Final response format

End every use of this skill with:

```markdown
# Blocked External Evidence Packet Result

Packet status: READY_FOR_RESUME | NOT_READY
Run ID: <RUN_ID>
Item ID: <ITEM_ID>
Evidence directory: `<LOCAL_EVIDENCE_DIR>`

## What the blocked item required

<Brief summary from escalation manifest and item context.>

## What was collected

- `<relative path>` — <what it proves>
- `<relative path>` — <what it proves>

## Owner collaboration

- <owner action requested/performed>
- <none>

## Validation performed

- <check> — pass | fail | not run
- <check> — pass | fail | not run

## Limitations

- <limitation>
- none

## Instructions for the orchestrator

Use this exact local evidence directory:

`<LOCAL_EVIDENCE_DIR>`

Recommended command:

python automation/run_plan_orchestrator.py resume --run-id <RUN_ID> --external-evidence-dir <LOCAL_EVIDENCE_DIR>

Supervised alternative:

python automation/run_plan_orchestrator.py supervise resume --run-id <RUN_ID> --external-evidence-dir <LOCAL_EVIDENCE_DIR>

## Human owner next step

<What the owner should do now.>
```

## Stop conditions

Stop and mark `NOT_READY` when:

- you cannot identify the current blocked item,
- the run is not actually blocked on external evidence,
- the evidence requirement cannot be determined,
- the owner cannot or will not provide required owner-only evidence,
- source evidence is stale, contradictory, or not authoritative enough,
- the packet would need secrets or unsafe data to be useful,
- required operations would be destructive or unauthorized,
- the evidence cannot be captured locally.

In these cases, still prepare a partial packet when useful and clearly list the missing evidence.
