# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Phase 7 — External file access (2026-05-14):
  - `research_manager/tools/external_access.py` — session-level whitelist for directories outside the workspace, seeded from `RM_EXTERNAL_READ_PATHS` env var and extensible via REPL
  - `research_manager/tools/writing_tools.py` — new `read_external_file` and `list_external_allowed` tools; relaxed extension list for external reads (adds `.ipynb`, `.html`, `.toml`, `.cfg`, etc.)
  - CLI: REPL commands `/allow <dir>`, `/allowed`, `/deny <dir>`; banner shows seeded external paths
  - System prompt teaches the agent the approval protocol (do not retry blindly on a denied read)
- Phase 6 — Project-specific tools (2026-05-14):
  - `research_manager/tools/dynamic_tools.py` — `propose_script`, `save_proposed_script`, `revise_script`, `run_saved_script`, `list_proposals`
  - Proposals are written to `res/_proposals/<id>.{py,R,sh}` with a sidecar `<id>.json` and cleaned up on promotion
  - CLI: `_on_tool_call` intercepts `propose_script` results, shows a syntax-highlighted preview, and prompts `(y/N/edit/rename)` plus a follow-up overwrite/rename/cancel on name collisions
  - CLI: `--auto-approve` flag for batch/CI sessions (skips proposal prompts; does NOT apply to external reads)
- `easy-research clean [path]` subcommand — confirm-before-delete cleanup of workspace contents, preserving `.env` and `.env.example` (2026-05-14)
- `init_workspace` now drops a `.env.example` into freshly initialized workspaces when no `.env` exists (2026-05-14)

### Fixed
- `easy-research` now finds `.env` relative to the user's current working directory rather than the installed `cli.py` location (`find_dotenv(usecwd=True)`) — previously the console script could fail with "OPENAI_API_KEY is not set" even when `.env` was present in the project directory (2026-05-14)
- Tool-schema generator now emits valid `items` for arrays, including bare `list` annotations — DeepSeek's strict schema validator was rejecting `add_task.depends_on` for missing `items` (2026-05-14)

- Phase 2–5 implementation (2026-05-14):
  - `research_manager/executor/runner.py` — subprocess runner with conda activation (`conda run -n <env>`), SIGTERM→SIGKILL on timeout, before/after file snapshot to detect new/modified files under `res/` and `report/`
  - `research_manager/tools/code_tools.py` — `run_python`, `run_r`, `run_shell` LLM tools
  - `research_manager/tools/writing_tools.py` — `list_results`, `read_text_file`, `write_report`, `append_report`, `list_reports`
  - `research_manager/tools/project_tools.py` — `init_project`, `validate_project`, `list_workspace`, `add_task`, `list_tasks`, `update_task_status`
  - `research_manager/workspace/manager.py` — workspace scaffolding + persistent task state file (`.research_manager_state.json`)
  - `research_manager/planner/task_graph.py` — DAG with cycle detection, topological sort, `runnable()` query
  - `research_manager/context.py` — shared workspace path (env var or CLI-set)
  - `research_manager/llm/prompts.py` — added `ARTICLE_WRITING_PROMPT`, `BLOG_WRITING_PROMPT`, `BOOK_WRITING_PROMPT` and `writing_prompt_for(mode)`
  - CLI: subcommands `init`, `validate`, `batch`; flags `-m/--mode`, `-f/--file`, `-o/--output`; REPL commands `/mode`, `/workspace`
  - `examples/quickstart.md` — end-to-end walkthrough
- Phase 1 foundation (2026-05-14):
  - `pyproject.toml` with `easy-research` CLI entry point
  - `research_manager/tools/registry.py` — tool registration with auto JSON schema (ported from ai4math)
  - `research_manager/llm/client.py` — `ResearchLLMClient` with tool-calling loop, RM_ env prefix
  - `research_manager/llm/prompts.py` — base system prompt for the research agent
  - `research_manager/cli.py` — interactive REPL + single-question mode with rich output
  - `.env.example` — environment variable template
- Initial project design and README
- Workspace directory template (`demo_work_dir/`)
- Project roadmap with phased implementation plan
- CLAUDE.md for session continuity
