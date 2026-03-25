# Security Policy

## Reporting a vulnerability

Please do not open a public GitHub issue for undisclosed security vulnerabilities.

Use GitHub private vulnerability reporting for this repository if it is enabled. If it is not enabled, contact the maintainer privately through GitHub first and include:

- a concise description of the issue
- affected files or commands
- reproduction steps
- security impact
- any suggested mitigation

## Scope notes

Security-sensitive areas in this repository include:

- shell command execution during verification
- git worktree creation and refresh behavior
- path validation and write-scope enforcement
- handling of local evidence artifacts
- prompt and schema contracts that influence mutation and audit behavior

We will aim to acknowledge reports promptly and coordinate a fix before public disclosure when the issue is valid.

