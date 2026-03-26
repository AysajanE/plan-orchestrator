# Comparison

This repo is easiest to evaluate against the tools engineers already know.
It should win only when you care more about approved inputs, isolation, and explicit stop states than about raw convenience or broad agent autonomy.

## Short version

If you want the most convenient interactive coding assistant, use Claude Code.
If you want the broadest open agent SDK / CLI / cloud surface, use OpenHands.
If you want issue-first trajectories and benchmark-oriented issue solving, use SWE-agent.
If you want repo-native automation inside an existing CI/CD estate, use GitHub Actions.
If you want zero abstraction and total flexibility, use manual `git worktree` + a CLI agent + CI.

Use `plan-orchestrator` when you want:

- one reviewed Markdown playbook as the public input contract
- one orchestrator-owned worktree per item attempt
- verification before dual audit
- dual audit over the same frozen packet
- deterministic merged findings before triage
- explicit `awaiting_human_gate`, `blocked_external`, and `escalated` terminals
- local/offline-first defaults and no agent-owned git operations

## Decision table

| comparator | stronger there | where `plan-orchestrator` is stronger | best fit for this repo |
|---|---|---|---|
| Claude Code | interactive coding, direct git workflows, and broad everyday convenience | approved-input discipline, frozen audit packets, worktree-per-item isolation, and explicit stop states | teams that already like coding agents but want harder review and recovery boundaries around repo changes |
| OpenHands | broader SDK/CLI/cloud product surface, remote execution, and tool integration breadth | smaller and more legible repo-change control model, Markdown reviewability, and local/offline-first posture | engineers who want a narrow runtime for governed repo changes instead of a larger agent platform |
| SWE-agent | issue-first mental model, benchmark lineage, and trajectory tooling | reviewed playbook ingestion, verification-before-audit, dual audit over a frozen packet, and human/external gating | operators who want to control an approved sequence of repo changes rather than optimize for issue-resolution benchmarks |
| GitHub Actions | ubiquity, org familiarity, hosted runners, and repo-native automation | item-level mutation semantics, explicit worktree isolation, merged findings, triage, and bounded fix/remediation loops | repos that need agent-aware change control instead of just workflow automation |
| manual `git worktree` + CLI agent + CI | lowest abstraction cost and maximum flexibility | repeatable structure, run state, artifact manifests, checkpoint commits, deterministic findings merge, and explicit terminal bundles | teams that have already outgrown tacit conventions and want the workflow written down as a runtime |

## What this repo should not overclaim

- It is not a better everyday coding assistant than Claude Code.
- It is not broader than OpenHands.
- It is not a benchmark-first issue-solving system.
- It is not easier to adopt than GitHub Actions inside an org already standardized on Actions.
- It is not simpler than the manual baseline.

## The honest pitch

Use this repo when you want boring change control for AI-assisted repo work.
Do not use it when you just want the fastest interactive path to a bug fix.
