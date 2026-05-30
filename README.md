# Research Manager

An AI agent that manages research projects end-to-end: executing analysis code, orchestrating LLM calls, and producing publication-ready outputs (paper drafts, book chapters, blog posts).

## Quick Start

```bash
# 1. Install from GitHub
git clone https://github.com/Landau1994/research_manager.git
cd research_manager
pip install -e .

# Or install directly with pip (no clone):
# pip install git+https://github.com/Landau1994/research_manager.git

# 2. Configure (uses an OpenAI-compatible API; defaults to DeepSeek)
cp .env.example .env
# edit .env and set OPENAI_API_KEY=...

# 3. Create a project workspace
mkdir my_project && cd my_project
easy-research init .   # also drops a .env.example into the new workspace

# 4. Drop your data into data/ and your scripts into script/, then chat:
easy-research
```

Inside the REPL:
```
you > run script/analyze.py inside the conda env "data-sci"
you > /mode article
you > draft an introduction based on res/txt/summary.csv
you > /quit
```

One-shot and batch modes:
```bash
easy-research "summarize what's in res/"            # single question
easy-research -m blog -f prompt.md -o out.md        # blog mode, file in, file out
easy-research batch questions.md -w 4               # parallel batch
```

See [examples/quickstart.md](examples/quickstart.md) for a fuller walkthrough.

## Motivation

Research workflows involve repetitive cycles of data processing, result interpretation, and writing. This agent automates the glue between these stages — running scripts, collecting results, and drafting structured documents — while keeping the researcher in control of decisions.

## Core Capabilities

- **Code Execution** — Run Python/R analysis scripts with subprocess isolation, collect outputs (figures, tables, data objects)
- **LLM-Powered Writing** — Generate and iteratively refine drafts for articles, books, or blogs based on analysis results
- **Project Management** — Track project state, manage dependencies between analysis steps, and maintain a structured workspace
- **Multi-Format Output** — Produce Markdown, LaTeX, or HTML outputs organized by publication type

## Project Workspace Structure

Each managed project follows a standardized layout:

```
project_dir/
├── data/              # Raw/input data
├── script/            # Analysis and processing scripts
├── res/               # Results and intermediate outputs
│   ├── fig/           # Figures and visualizations
│   ├── h5ad/          # HDF5 annotated data (bioinformatics)
│   ├── python_obj/    # Serialized Python objects
│   ├── r_obj/         # Serialized R objects
│   └── txt/           # Text-based results (tables, logs)
├── report/            # Final outputs
│   ├── article/       # Academic paper drafts
│   ├── blog/          # Blog posts
│   └── book/          # Book chapters/manuscripts
└── packages/          # Generated pip-installable packages
```

## Architecture

```
research_manager/
├── research_manager/
│   ├── llm/
│   │   ├── client.py          # OpenAI-compatible client + tool-calling loop
│   │   └── prompts.py         # Base + article/blog/book writing modes
│   ├── executor/
│   │   └── runner.py          # Subprocess runner with conda activation and file tracking
│   ├── tools/
│   │   ├── registry.py        # @tool decorator + auto JSON-schema generation
│   │   ├── code_tools.py      # run_python, run_r, run_shell
│   │   ├── writing_tools.py   # list_results, read_text_file, write_report, polish_text, add_citations, data_availability, ...
│   │   ├── project_tools.py   # init_project, list_workspace, add_task, ...
│   │   ├── dynamic_tools.py   # propose_script, save_proposed_script, revise_script, run_saved_script
│   │   ├── package_tools.py   # build_package, confirm_package_build
│   │   ├── env_tools.py       # scan_dependencies, plan_environment, apply_environment_plan
│   │   └── external_access.py # session whitelist for read_external_file
│   ├── planner/
│   │   └── task_graph.py      # DAG with cycle detection and topological sort
│   ├── workspace/
│   │   └── manager.py         # init/validate workspace, persist task state
│   ├── sessions.py            # Auto-save (3 rotating slots) + manual save/load
│   ├── context.py             # Current workspace path (shared runtime context)
│   └── cli.py                 # REPL, single-question, batch, init, validate, clean
├── examples/quickstart.md
├── demo_work_dir/             # Example workspace template
├── pyproject.toml
└── README.md
```

## Design Principles

1. **Tool-calling loop** — The LLM operates in an iterative loop: reason → call tools → observe results → continue. Borrowed from the ai4math pattern of structured tool orchestration.
2. **Subprocess isolation** — All user scripts run in isolated subprocesses with timeouts and graceful shutdown, preventing crashes from affecting the agent.
3. **Structured planning** — Complex projects are decomposed into a dependency graph of tasks before execution begins.
4. **Result-aware writing** — The writing stage has direct access to execution outputs (figures, tables, statistics), enabling grounded drafts rather than hallucinated content.
5. **Researcher-in-the-loop** — The agent proposes plans and drafts; the researcher approves, edits, or redirects at each stage.

## Intended Workflow

```
1. Initialize project workspace
2. User provides: data + analysis scripts + writing goal
3. Agent plans execution order (resolves script dependencies)
4. Agent executes scripts, collects results
5. Agent drafts document sections using results as context
6. User reviews, provides feedback
7. Agent revises until approved
8. Final output written to report/
```

## Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key for LLM provider | (required) |
| `OPENAI_BASE_URL` | API endpoint | `https://api.deepseek.com/beta` |
| `RM_MODEL` | Primary model | `deepseek-v4-pro` |
| `RM_MAX_TOKENS` | Max tokens per LLM response | `8192` |
| `RM_TEMPERATURE` | Sampling temperature | `0.0` |
| `RM_MAX_ITERATIONS` | Max tool-calling loop iterations | `30` |
| `RM_REASONING_EFFORT` | DeepSeek reasoning level (`none`/`low`/`medium`/`high`) | `high` |
| `RM_TOOL_TIMEOUT` | Default script/tool execution timeout (s) | `300` |
| `RM_WORKSPACE` | Override workspace path | (current dir) |
| `RM_EXTERNAL_READ_PATHS` | Colon-separated directories the agent may read via `read_external_file` | (empty) |
| `RM_RECORD` | `1` to record a trajectory to `.research_manager_sessions/trajectories/` | (off) |
| `RM_RECORD_KEEP` | Rolling window: how many recent trajectories to keep | `50` |
| `RM_RECORD_COUNTERFACTUALS` | `1` to also shadow-sample one alternate completion per LLM call | (off) |
| `RM_COUNTERFACTUAL_TEMP` | Temperature used for counterfactual samples | `0.7` |
| `CONDA_EXE` | Path to conda executable | (from PATH) |

## CLI

```
easy-research                          # interactive REPL
easy-research "question"               # single question
easy-research -m article "draft intro" # writing mode
easy-research -f question.md           # read from file
easy-research init [path]              # create workspace layout
easy-research validate [path]          # check workspace structure
easy-research clean [path]             # wipe workspace contents (keeps .env)
easy-research batch questions.md -w 4  # parallel batch mode
easy-research --auto-approve ...       # skip script-proposal prompts
easy-research --record                 # record a trajectory for future RL data
easy-research sessions list            # list saved sessions + trajectories
easy-research sessions trace <id>      # summary of a recorded trajectory
easy-research sessions analyze <id>    # Tier 2 derived signals as JSON
easy-research sessions prune --keep N  # manually trim trajectories
```

REPL commands: `/tools`, `/mode <kind>`, `/workspace`, `/allow <dir>`, `/allowed`, `/deny <dir>`, `/package <name>`, `/env scan|plan`, `/sessions`, `/save [name]`, `/load <name>`, `/branch [name]`, `/good [note]`, `/bad [note]`, `/outcome <kind>`, `/redo`, `/reset`, `/help`, `/quit`.

### Reading files outside the workspace

By default the agent can only read files under the project workspace. To grant
access to external paths (e.g. a reference dataset under `~/data/`):

```bash
# Persistent: set in .env or shell
export RM_EXTERNAL_READ_PATHS=/path/to/refs:/another/dir
easy-research

# Or interactively, inside the REPL:
you > /allow ~/data/refs
you > read the schema in ~/data/refs/manifest.json
```

The agent uses the `read_external_file` tool for these reads. Approval is per
directory subtree — approving `/tmp/ext_data` does not approve `/tmp/secret.txt`.

See [examples/quickstart.md](examples/quickstart.md) for a walkthrough.

## Roadmap

### Phase 1: Foundation ✅
- [x] Project scaffolding (`pyproject.toml`, package structure)
- [x] LLM client (OpenAI-compatible, tool-calling loop)
- [x] Tool registry system (decorator-based registration, schema generation)
- [x] Basic CLI (interactive mode)

### Phase 2: Code Execution ✅
- [x] Script runner with subprocess isolation
- [x] Conda environment detection and activation
- [x] Timeout and graceful shutdown handling (SIGTERM → SIGKILL)
- [x] Result collection (stdout, stderr, new/modified files)
- [x] Python and R script support (both via conda)

### Phase 3: Writing Pipeline ✅
- [x] System prompts for article/blog/book writing styles
- [x] Result-aware context injection (list_results, read_text_file tools)
- [x] Section-by-section drafting (write_report / append_report)
- [x] Markdown output to `report/{article,blog,book}/`
- [x] Nature-style upgrade for **article mode only**: argument-first prompt + three helper tools (`polish_text`, `add_citations`, `data_availability`) distilled from the `nature-skills` instruction bundles. Blog and book modes are unchanged because Nature conventions would distort their register. The three helpers are hidden from the LLM's tool schema in blog/book/base mode, so they cannot be called by mistake.

### Phase 4: Project Management ✅
- [x] Workspace initialization (`easy-research init`) and validation
- [x] Task dependency graph (DAG with cycle detection)
- [x] Topological order + runnable-task resolution
- [x] Project state persistence (`.research_manager_state.json`)

### Phase 5: Polish ✅
- [x] Batch mode (`easy-research batch` with parallel workers)
- [x] Workspace template via `init` subcommand
- [x] Writing-mode CLI flag (`-m article|blog|book`) and `/mode` REPL command
- [x] Documentation and examples (`examples/quickstart.md`)

### Phase 6: Project-Specific Tools ✅

Allow the agent to author **ad-hoc, project-specific code** during a session,
execute it, show the result, and then ask the user whether to *promote* the
snippet into a permanent script under `script/`.

Motivation: the original built-in tools are generic (run_python, write_report, …).
Real projects accumulate domain-specific operations (e.g. "normalize this lab's
flow-cytometry export", "compute the in-house QC score"). Today the user has
to hand-edit `script/*.py`. Phase 6 lets the LLM propose, run, and persist
those snippets through the same conversational loop.

Tools (registered under category `dynamic`):

1. **`propose_script(name, language, code, description, run, conda_env, timeout)`**
   — LLM submits a snippet. It is written to `res/_proposals/<id>.{py,R,sh}`
   with a sidecar `<id>.json` carrying metadata. If `run=True`, the snippet
   is executed inside the workspace using the existing subprocess runner
   (conda activation, timeout, file-diff). The tool returns the proposal id
   and execution result; it does NOT save the snippet anywhere outside
   `_proposals/` — that step is the REPL's responsibility.

2. **REPL confirmation** — when `_on_tool_call` sees a `propose_script` call,
   the REPL pauses, shows a syntax-highlighted preview, and prompts:
   ```
   save proposal <id> → script/<name>.{py,R,sh}? (y/N/edit/rename):
   ```
   - `y` → call `save_proposed_script(id)` automatically.
   - `edit` → open `$EDITOR` on the proposal file, then save.
   - `rename` → ask for a new filename, then save.
   - `N` → leave the proposal in `res/_proposals/` for later.

   If `script/<name>` already exists, a second prompt offers
   *overwrite / rename / cancel*. On overwrite, the prior version is backed
   up into `res/_proposals/<stem>_backup_<ts>.<ext>`.

3. **`save_proposed_script(proposal_id, target_name, overwrite)`** — promote a
   proposal to `script/<name>`. The proposal file and its `.json` sidecar are
   removed once promoted.

4. **`revise_script(name, new_code, description, run, conda_env, timeout)`** —
   LLM can rewrite an existing `script/<name>`. The previous version is
   always copied to `res/_proposals/<stem>_rev_<ts>.<ext>` first, then the
   new code is written (and optionally executed).

5. **`run_saved_script(name, conda_env, timeout)`** — once promoted, the
   agent can invoke the script by name in later turns and chain it into the
   task DAG via `add_task`.

6. **`list_proposals()`** — list pending (unpromoted) proposals so the agent
   can describe what is waiting for a decision.

Safety:
- Proposed code is shown verbatim before execution (syntax-highlighted panel).
- Snippets that try to write outside the workspace root are rejected.
- A `--auto-approve` CLI flag (off by default) skips the prompt for trusted
  batch/CI sessions.
- Backups of overwritten scripts always land in `res/_proposals/`.

### Phase 7: External File Access ✅

By default the agent can only read files under the project workspace. Phase 7
adds opt-in access to specific external directories.

- **`read_external_file(path, max_chars)`** — read a text file outside the
  workspace iff the path lies under a directory the user has explicitly
  approved.
- **`list_external_allowed()`** — let the agent introspect the current
  whitelist before asking for more access.
- **Whitelist sources**:
  - `RM_EXTERNAL_READ_PATHS` env var (colon-separated absolute paths, loaded
    at startup).
  - REPL commands `/allow <dir>`, `/allowed`, `/deny <dir>` for in-session
    changes.
- Approval is **per directory subtree** — approving `/tmp/ext_data` does not
  approve `/tmp/secret.txt`.
- `--auto-approve` does **not** apply here: external reads always require
  explicit pre-approval.

### Phase 8: Package Builder ✅

Promote workspace scripts into pip-installable Python packages under
`packages/<name>/`.

Tools (category `dynamic`):

- **`build_package(package_name, description, scripts, version, dependencies)`**
  — validates inputs and returns a file manifest for user confirmation (no
  files written yet). Returns `needs_user_confirmation: true`.
- **`confirm_package_build(..., overwrite)`** — writes the full package
  structure: `pyproject.toml` (setuptools src-layout), `src/<name>/` with
  copied scripts and `__init__.py`, `README.md`, `tests/` with a stub.

REPL command `/package <name>`:
```
you > /package my_analysis
scripts in workspace:
  1. clean.py
  2. utils.py
include which scripts? (comma-separated numbers, 'all', or 'none'): all
description: Analysis utilities
dependencies (comma-separated, or empty): numpy>=1.24
build? (y/N): y

built → packages/my_analysis/
```

LLM tool-call path: `_on_tool_call` intercepts `build_package` results, shows
the manifest panel, prompts y/N, and calls `confirm_package_build` on approval.

If the package already exists, the user is offered overwrite (with backup) or
cancel.

### Phase 10: Conda Environment Setup ✅

Scan workspace scripts for imported dependencies, generate three install
plans, let the user pick one, and optionally execute it.

Tools (category `dynamic`):

- **`scan_dependencies(include_packages)`** — walks `script/` (and optionally
  `packages/*/src/`), parses Python files with `ast` (filters stdlib via
  `sys.stdlib_module_names`) and R files with regex on `library()` /
  `require()`. Returns top-level Python imports + R packages plus a
  per-file breakdown.
- **`plan_environment(python_packages, r_packages, target_env, python_version, create_new)`**
  — generates three plans side-by-side:
  - **Plan A: conda-only** — `conda install -c conda-forge ...`; packages
    not available there are listed in `fallback_pypi_only`
  - **Plan B: mixed** — conda for what it can resolve, `pip install` for
    the rest (a small built-in `_PYPI_ONLY` set)
  - **Plan C: environment.yml** — full `environment.yml` text plus the
    `conda env create -f environment.yml` command
  Import-name normalization is built in (e.g. `cv2` → `opencv-python`,
  `sklearn` → `scikit-learn`).
- **`apply_environment_plan(plan, target_env, ..., execute)`** — either
  prints the chosen plan's commands (`execute=false`) or runs them via
  `ScriptRunner.run_shell()` with a 1800s timeout (`execute=true`). For
  Plan C, writes `environment.yml` to the workspace root.

REPL command `/env`:
```
you > /env scan
  python: cv2, numpy, openai, pandas, sklearn
  R:      dplyr, ggplot2

you > /env plan
  target env (existing name, or 'new:<name>'): new:my_proj
  python version [3.11]:
  → shows all three plans in syntax-highlighted panels
  choose plan [A/B/C/cancel]: B
  execute now? (y/N/show): show
  → renders the commands; user can copy or re-run with y
```

LLM tool-call path: `_on_tool_call` intercepts `plan_environment` results,
shows the three panels, and walks the user through the choose/execute
prompts (same shape as the package-builder intercept).

### Phase 9: Session Persistence ✅

Conversations are automatically saved and can be manually preserved or restored.

- **Auto-save**: 3 rotating slots under
  `.research_manager_sessions/auto/slot_{0,1,2}.json`. The oldest slot is
  recycled on each new session. Written after every successful chat turn.
- **Manual save**: `/save [name]` writes to
  `.research_manager_sessions/saved/<name>.json`. These are never
  auto-deleted or overwritten.
- **Load**: `/load <name>` restores messages and mode (accepts `auto-0`
  through `auto-2` or any saved name).
- **`/sessions`**: lists all available sessions with turn count, model, mode,
  and last-updated timestamp.
- **Exit prompt**: on `/quit` or Ctrl-C/Ctrl-D, if the conversation has user
  turns, the REPL asks whether to manually save before exiting.

### Phase 11: Trajectory Recorder ✅

A passive recorder layered onto the existing tool-calling loop. Every real
project run produces structured data future-usable for MCTS warm-start, SFT,
or DPO — without changing the user-facing flow. Four tiers, increasing cost
and value.

Storage layout:
```
.research_manager_sessions/
├── trajectories/<session_id>/
│   ├── meta.json           # model, git commit, mode, started_at, n_steps, outcome
│   ├── events.jsonl        # append-only raw event stream
│   └── snapshots/
│       └── step_<i>.json   # {path: sha256} manifest at each tool_call boundary
└── objects/<sha[:2]>/<sha[2:]>   # content-addressed blob store, shared across sessions
```

Enable: `easy-research --record` or `RM_RECORD=1`. Inspect:
`easy-research sessions list|trace <id>|analyze <id>|prune --keep N`.

#### Tier 1 — Protocol-layer recorder (opt-in, ~zero overhead) ✅
- [x] `research_manager/recording/recorder.py` — `TrajectoryRecorder` class:
      events.jsonl writer + content-addressed snapshot manager.
- [x] Event types: `user_message`, `llm_request`, `llm_response`,
      `tool_call_start`, `tool_call_end`, `subprocess_exit`, `user_label`,
      `counterfactual`.
- [x] Hook via existing `on_tool_call` / `on_response` callbacks on
      `ResearchLLMClient.chat()` — **no core changes**. Plus
      `client.set_recorder(rec)` to attach a recorder.
- [x] Tier 1.5 — `executor/runner.py` emits `subprocess_exit` events
      (returncode, timed_out, duration, stdout/stderr hashes) via a
      module-level `set_active_recorder` hook.
- [x] Snapshot at every tool_call boundary (not message boundary) — content
      dedup keeps storage small. Also a step_0 snapshot at session start.
- [x] Store `tool_call.arguments` as raw JSON string, never re-serialized
      (preserves byte-level reproducibility).
- [x] Retention: keep last `RM_RECORD_KEEP` (default 50) trajectories;
      content-addressed `objects/` are shared across sessions.

#### Tier 2 — Derived behavior signals (offline, free) ✅
Computed by `research_manager/recording/analysis.py` from Tier 1 raw events;
no runtime cost. Run via `easy-research sessions analyze <id>`.
- [x] User-edits-after-agent diff (high-quality supervised correction signal).
- [x] File survival rate (agent-written files alive at session end vs
      overwritten/deleted).
- [x] Rerun pattern detection (same tool + same args_hash run twice → first run
      likely failed).
- [x] Tool-output citation graph (which later tool_calls reference earlier
      output paths in their args).
- [x] Lightweight intent classifier on user messages
      (`不对 / 重做 / instead / wrong / redo` → implicit reject of last LLM turn).

#### Tier 3 — Explicit reward gestures (opt-in, gold standard) ✅
- [x] `/good [note]` and `/bad [note]` — label most recent LLM response.
- [x] `/outcome success|partial|fail` — label entire session at end.
- [x] `/redo` — regenerate last response after recording a `redo_reject`
      label that captures the prior assistant turn count (free preference-pair
      seed).
- [x] `/branch [name]` — fork: snapshot current messages + workspace state
      under a new saved-session name; current session keeps recording.

#### Tier 4 — Counterfactual sampling (opt-in, ~30% extra completion tokens) ✅
- [x] `RM_RECORD_COUNTERFACTUALS=1` toggle (additional to `RM_RECORD=1`).
- [x] At each LLM call, also sample one alternate completion at
      `RM_COUNTERFACTUAL_TEMP` (default 0.7); record without executing.
- [x] Yields `(state, chosen, rejected)` triples — DPO-ready format,
      reconstructible from `events.jsonl` + `objects/`.

#### Wiring & CLI ✅
- [x] `--record` CLI flag and `RM_RECORD=1` env var.
- [x] `easy-research sessions list` — show saved sessions + recorded trajectories.
- [x] `easy-research sessions trace <id>` — print trajectory summary.
- [x] `easy-research sessions analyze <id>` — Tier 2 derived signals as JSON.
- [x] `easy-research sessions prune --keep N` — manual retention override.
- [x] REPL: `/good /bad /outcome /redo /branch` slash commands.

#### Resolved decisions (2026-05-30)
- [x] **Default**: opt-in via `--record` flag or `RM_RECORD=1` env var.
      Nothing is recorded unless the user explicitly enables it.
- [x] **Tier 4 default**: off. Counterfactual sampling requires
      `RM_RECORD_COUNTERFACTUALS=1` (and `RM_RECORD=1`) on top.
- [x] **Storage path**: `.research_manager_sessions/trajectories/<session_id>/`
      — reuses the existing sessions directory.
- [x] **Retention**: rolling window of the last 50 trajectories per workspace.
      Oldest trajectories are pruned on new-session creation. Manual override
      via `easy-research sessions prune --keep N`. Content-addressed `objects/`
      are GC'd when no surviving snapshot references them.
- [x] **`/branch`**: yes. REPL command copies current messages + latest
      workspace snapshot manifest to a new session_id; both branches continue
      independently. Cheap to implement on top of the snapshot store.

#### Out of scope for Phase 11
The MCTS search loop itself, scbench integration, and any RL training are
**not** part of this phase. The active-search alternative was considered
but shelved in favor of the passive recorder, which collects RL-training
data from real sessions without the cost of running full simulations.

### Completed
- [x] Define workspace directory structure (`demo_work_dir/`) — 2026-05-14
- [x] Initial README and project design — 2026-05-14
- [x] Phase 1 — Foundation (pyproject.toml, LLM client, tool registry, CLI) — 2026-05-14
- [x] Phase 2 — Code execution (subprocess runner, conda integration) — 2026-05-14
- [x] Phase 3 — Writing pipeline (mode prompts, report tools) — 2026-05-14
- [x] Phase 4 — Project management (workspace, task graph, state) — 2026-05-14
- [x] Phase 5 — Polish (batch mode, examples) — 2026-05-14
- [x] Phase 6 — Project-specific tools (propose/save/revise/run dynamic scripts) — 2026-05-14
- [x] Phase 7 — External file access (read_external_file + whitelist) — 2026-05-14
- [x] Phase 8 — Package builder (build_package + /package command) — 2026-05-14
- [x] Phase 9 — Session persistence (auto-save, /save, /load, /sessions) — 2026-05-14
- [x] Phase 10 — Conda environment setup (scan_dependencies + plan_environment + /env) — 2026-05-14
- [x] `easy-research clean` subcommand and `.env.example` auto-generation in `init` — 2026-05-14
- [x] `.env` discovery fixed for installed console script (use cwd, not module path) — 2026-05-14
- [x] Tool-schema generator now emits valid `items` for bare `list` annotations — 2026-05-14

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full history.

## Status

Phases 1–11 implemented. The CLI installs as `easy-research` and exposes 29
LLM-callable tools across `code`, `writing`, `project`, and `dynamic`
categories. Article-mode prompt and helpers are tuned to Nature/Nature
Communications writing patterns (argument-first, hourglass, bounded claims).
Trajectory recording (opt-in via `--record` / `RM_RECORD=1`) captures the full
event stream + content-addressed workspace snapshots for future RL/MCTS work.

## License

TBD
