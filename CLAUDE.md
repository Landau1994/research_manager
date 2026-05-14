# CLAUDE.md

Project-level instructions for Claude Code sessions working on this repository.

## Project Overview

**Research Manager** — an AI agent for managing research projects: executing analysis code, orchestrating LLM calls, and producing publication-ready documents (papers, books, blogs).

## Key References

- `demo_work_dir/` — canonical workspace template (data/script/res/report/packages structure)
- `/home/wlt2025/project/ai4math` — reference implementation for LLM client, tool registry, subprocess isolation, and CLI patterns. Mirror these architectural patterns.

## Architecture Decisions

- **LLM Client**: OpenAI-compatible API (supports DeepSeek, OpenAI, etc.) with tool-calling loop
- **Tool System**: Decorator-based registration with automatic JSON schema generation (from ai4math)
- **Execution**: Subprocess isolation with timeout + SIGTERM/SIGKILL for all user scripts; conda for Python/R environment management (activate target env before running scripts)
- **Planning**: DAG-based task dependency resolution before execution
- **Writing**: Result-aware drafting — LLM sees actual outputs (figures, tables) when writing
- **Dynamic Tools**: Two-phase pattern (propose → user confirm → save) for script proposals and package builds
- **Session Persistence**: 3-slot auto-save + manual save/load under `.research_manager_sessions/`

## Conventions

- Package name: `research_manager`
- Environment variable prefix: `RM_`
- Python 3.11+
- Use `conda` for Python and R environment management
- Tests in `tests/` directory, run with `pytest`
- Code style: ruff for linting and formatting

## Current State

Phases 1–10 complete. 26 LLM-callable tools registered across `code`, `writing`, `project`, and `dynamic` categories.

Key components:
- `executor/runner.py` — subprocess runner with `conda run -n <env>`, SIGTERM/SIGKILL, before/after file diff
- `tools/{code,writing,project,dynamic,package,env}_tools.py` — six tool modules auto-registered via `tools/__init__.py`
- `tools/external_access.py` — session whitelist for `read_external_file`
- `workspace/manager.py` — `init_workspace`, `validate_workspace`, state file load/save
- `planner/task_graph.py` — DAG with cycle detection
- `context.py` — `get_workspace()` / `set_workspace()` resolves to CLI dir, `RM_WORKSPACE` env, or cwd
- `sessions.py` — auto-save (3 rotating slots), manual save/load, session listing
- `llm/prompts.py` — `BASE_SYSTEM_PROMPT` plus `writing_prompt_for("article"|"blog"|"book")`

Possible next steps if extending:
- Tests (`tests/` directory is empty)
- Streaming responses (current implementation collects full reply)
- Export to LaTeX/PDF (current output is Markdown only)
- Resumable task graph execution (autonomous mode that walks runnable tasks)

## After Every Change

1. Update `README.md` to reflect new features, commands, tools, or architecture changes
2. Update `CHANGELOG.md` under `[Unreleased]` with what was added/fixed
3. Commit with a descriptive message and push to `origin/main`
4. Keep this file (`CLAUDE.md`) in sync if the change affects architecture, tool count, or conventions

## When Starting a New Session

1. Read this file and `README.md` for context
2. Check `CHANGELOG.md` for recent changes
3. Run `git log --oneline -10` if git is initialized
4. Check the roadmap in README.md for current phase
