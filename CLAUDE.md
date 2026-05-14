# CLAUDE.md

Project-level instructions for Claude Code sessions working on this repository.

## Project Overview

**Research Manager** — an AI agent for managing research projects: executing analysis code, orchestrating LLM calls, and producing publication-ready documents (papers, books, blogs).

## Key References

- `demo_work_dir/` — canonical workspace template (data/script/res/report structure)
- `/home/wlt2025/project/ai4math` — reference implementation for LLM client, tool registry, subprocess isolation, and CLI patterns. Mirror these architectural patterns.

## Architecture Decisions

- **LLM Client**: OpenAI-compatible API (supports DeepSeek, OpenAI, etc.) with tool-calling loop
- **Tool System**: Decorator-based registration with automatic JSON schema generation (from ai4math)
- **Execution**: Subprocess isolation with timeout + SIGTERM/SIGKILL for all user scripts; conda for Python/R environment management (activate target env before running scripts)
- **Planning**: DAG-based task dependency resolution before execution
- **Writing**: Result-aware drafting — LLM sees actual outputs (figures, tables) when writing

## Conventions

- Package name: `research_manager`
- Environment variable prefix: `RM_`
- Python 3.11+
- Use `conda` for Python and R environment management
- Tests in `tests/` directory, run with `pytest`
- Code style: ruff for linting and formatting

## Current State

All planned phases (1–5) complete. 14 LLM-callable tools registered across `code`, `writing`, and `project` categories.

Key components:
- `executor/runner.py` — subprocess runner with `conda run -n <env>`, SIGTERM/SIGKILL, before/after file diff
- `tools/{code,writing,project}_tools.py` — three tool modules auto-registered via `tools/__init__.py`
- `workspace/manager.py` — `init_workspace`, `validate_workspace`, state file load/save
- `planner/task_graph.py` — DAG with cycle detection
- `context.py` — `get_workspace()` / `set_workspace()` resolves to CLI dir, `RM_WORKSPACE` env, or cwd
- `llm/prompts.py` — `BASE_SYSTEM_PROMPT` plus `writing_prompt_for("article"|"blog"|"book")`

Possible next steps if extending:
- Tests (`tests/` directory is empty)
- Streaming responses (current implementation collects full reply)
- Export to LaTeX/PDF (current output is Markdown only)
- Resumable task graph execution (autonomous mode that walks runnable tasks)

## When Starting a New Session

1. Read this file and `README.md` for context
2. Check `CHANGELOG.md` for recent changes
3. Run `git log --oneline -10` if git is initialized
4. Check the roadmap in README.md for current phase
