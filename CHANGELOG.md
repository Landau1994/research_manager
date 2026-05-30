# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Phase 11 — Trajectory recorder for future RL/MCTS data (2026-05-30):
  - `research_manager/recording/recorder.py` — `TrajectoryRecorder` class. Opt-in via `--record` or `RM_RECORD=1`. Writes `events.jsonl` (8 event types: `user_message`, `llm_request`, `llm_response`, `tool_call_start`, `tool_call_end`, `subprocess_exit`, `user_label`, `counterfactual`), per-tool-call `snapshots/step_NNNN.json` manifests, and a content-addressed `objects/` blob store shared across sessions. Rolling retention (default 50 trajectories, configurable via `RM_RECORD_KEEP`).
  - `research_manager/recording/analysis.py` — Tier 2 offline analysis: user-edits-after-agent diff, file survival rate, rerun pattern detection, tool-output citation graph, lightweight bilingual reject/accept intent classifier on user messages.
  - `research_manager/llm/client.py` — six event hooks in `chat()` plus `set_recorder()` and `_maybe_record_counterfactual()`; all hooks try/except wrapped so recording failures never break the conversation. Tier 4 counterfactual sampling shadow-samples one alternate completion at `RM_COUNTERFACTUAL_TEMP` (default 0.7) when `RM_RECORD_COUNTERFACTUALS=1`.
  - `research_manager/executor/runner.py` — emits `subprocess_exit` events (returncode, timed_out, duration, stdout/stderr hashes) via the module-level `set_active_recorder` hook. Runner stays decoupled — only imports `recording` lazily, no signature changes.
  - `research_manager/cli.py` — `--record` flag, `easy-research sessions list|trace|analyze|prune` subcommands, REPL slash commands `/good`, `/bad`, `/outcome`, `/redo`, `/branch`. Recorder is started in `_run_interactive` and `_run_oneshot` and closed on exit.
  - README updated: Phase 11 section, env-var table additions (`RM_RECORD`, `RM_RECORD_KEEP`, `RM_RECORD_COUNTERFACTUALS`, `RM_COUNTERFACTUAL_TEMP`), CLI commands, and REPL command list.
- Schema-level mode gating for article-only tools (2026-05-16):
  - `research_manager/llm/prompts.py` — `ARTICLE_ONLY_TOOLS = {polish_text, add_citations, data_availability}` and `excluded_tools_for(mode)` helper. Returns the gated set for `blog` / `book` / `base`, empty for `article`.
  - `research_manager/llm/client.py` — `ResearchLLMClient.excluded_tools` field and `set_excluded_tools()` method; `get_tools()` filters the registry schemas before sending to the LLM, so gated tools are not even visible to the model in non-article modes.
  - `research_manager/cli.py` — wired `excluded_tools_for(mode)` into `_make_client`, `/mode <name>`, `/load` (when the loaded session's mode differs), and `_batch_worker`. The `/mode` switch now prints the hidden-tool list.
  - Defense beyond the "ARTICLE MODE ONLY" docstring hint: previously the LLM could call `polish_text` etc. in blog/book mode and would just produce Nature-register prose; now the tool is absent from the schema so the call cannot be made.
- Nature-style article-mode upgrade (2026-05-15):
  - `research_manager/llm/prompts.py` — `ARTICLE_WRITING_PROMPT` rewritten to embed an argument-first / hourglass / paper-type-aware workflow distilled from the `nature-skills` instruction bundles (writing + polishing). Includes intake gates (core claim / evidence / boundary), section-specific defaults (abstract/intro/methods/results/discussion/conclusion/title), paragraph rules, and verb calibration ladder. `BLOG_WRITING_PROMPT` and `BOOK_WRITING_PROMPT` are deliberately left unchanged — Nature conventions would distort their register.
  - `research_manager/tools/writing_tools.py` — three new tools, all marked **article-mode only** in their docstrings so the LLM does not invoke them in blog/book mode:
    - `polish_text(text, focus)` — returns a focus-specific (abstract/introduction/methods/results/discussion/conclusion/title/general) rule set, paragraph self-check, and a structured instruction for the LLM to consume in its next turn. Deterministic; no network, no LLM-in-tool.
    - `add_citations(text, scope)` — segments prose into sentences, grades each as `primary_claim` / `qualitative_claim` / `quantitative_statement` / `background_or_transition`, emits an English search-query hint per claim, and attaches the scope-specific journal whitelist (Nature flagship, Nature family, CNS, CNS family, any-journal). Does not search the web — the LLM uses the worksheet to propose citations.
    - `data_availability(notes, journal)` — returns a Nature-policy Data Availability scaffold with explicit `[TODO: ...]` placeholders for DOIs/accessions/owners (never invented), the access-class taxonomy, and the FAIR/policy checklist.
  - Tool count: 26 → 29
- Phase 10 — Conda environment setup (2026-05-14):
  - `research_manager/tools/env_tools.py` — three new tools (category `dynamic`):
    - `scan_dependencies(include_packages)` — AST-based parsing of `script/*.py` for top-level imports (stdlib filtered via `sys.stdlib_module_names`) and regex-based parsing of `script/*.R` / `.r` for `library()` / `require()` calls; optional walk into `packages/*/src/`
    - `plan_environment(...)` — generates three install plans side-by-side: conda-only, conda+pip mixed, and `environment.yml`; small built-in `_IMPORT_TO_PIP` map for common name aliases (`cv2`→`opencv-python`, `sklearn`→`scikit-learn`, etc.) and `_PYPI_ONLY` set for packages that don't live in conda-forge
    - `apply_environment_plan(plan, ..., execute)` — renders or executes the chosen plan via `ScriptRunner.run_shell()` (1800s timeout); writes `environment.yml` to workspace root for plan C
  - CLI: REPL command `/env scan [--all]` and `/env plan`; `_on_tool_call` intercepts `plan_environment` results from the LLM and walks the user through choose-plan → execute prompts (same pattern as `build_package`)
  - Tool count: 23 → 26
- Phase 9 — Session persistence (2026-05-14):
  - `research_manager/sessions.py` — auto-save (3 rotating slots under `.research_manager_sessions/auto/`), manual save to `saved/`, load by name, list all sessions
  - CLI: REPL commands `/sessions`, `/save [name]`, `/load <name>`; after every successful chat turn the current slot is updated; on `/quit` or Ctrl-C/Ctrl-D, the REPL asks whether to manually save before exiting
- Phase 8 — Package builder (2026-05-14):
  - `research_manager/tools/package_tools.py` — `build_package` (validates inputs, returns manifest for user confirmation) and `confirm_package_build` (writes pyproject.toml, src layout, README, tests stub)
  - CLI: REPL command `/package <name>` with interactive script selection, dependency entry, and confirm-overwrite; `_on_tool_call` intercepts LLM-triggered `build_package` calls and prompts the user before writing
  - Workspace: `packages/` added to `WORKSPACE_DIRS`
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
