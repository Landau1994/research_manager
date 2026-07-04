# AGENTS.md

Repository-level instructions for Codex and other coding agents working in this
project.

## Project Context

Read `CLAUDE.md`, `README.md`, and `CHANGELOG.md` before making non-trivial
changes. `CLAUDE.md` contains the detailed project architecture and should stay
in sync with this file.

## Change Workflow

- Before a substantial change, run `git status --short --branch` and note any
  existing user changes. Do not overwrite unrelated local work.
- For feature, behavior, CLI, or architecture changes, update documentation in
  the same turn:
  - `README.md` for user-facing behavior, commands, tools, or architecture.
  - `CHANGELOG.md` under `[Unreleased]` for added, changed, fixed, or removed
    behavior.
  - `CLAUDE.md` when architecture, conventions, or project state changes.
- After a substantial change, run `git status --short --branch` again and report
  the changed files.
- Verify the change with the most relevant tests or checks available in the
  repository. If verification cannot be run, explain why.
- When the change is complete and verified, commit with a descriptive message
  and push the current branch to its remote. If push fails because credentials,
  network, or branch state require user input, report the exact blocker and the
  current git status.

## Git Discipline

- Keep commits focused on the current request.
- Do not revert or rewrite user changes unless the user explicitly asks.
- Prefer non-interactive git commands.
- Do not use destructive commands such as `git reset --hard` or `git checkout --`
  without explicit user approval.
