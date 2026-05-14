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
└── report/            # Final outputs
    ├── article/       # Academic paper drafts
    ├── blog/          # Blog posts
    └── book/          # Book chapters/manuscripts
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
│   │   ├── writing_tools.py   # list_results, read_text_file, read_external_file, write_report, ...
│   │   ├── project_tools.py   # init_project, list_workspace, add_task, ...
│   │   ├── dynamic_tools.py   # propose_script, save_proposed_script, revise_script, run_saved_script
│   │   └── external_access.py # session whitelist for read_external_file
│   ├── planner/
│   │   └── task_graph.py      # DAG with cycle detection and topological sort
│   ├── workspace/
│   │   └── manager.py         # init/validate workspace, persist task state
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
```

REPL commands: `/tools`, `/mode <kind>`, `/workspace`, `/allow <dir>`, `/allowed`, `/deny <dir>`, `/reset`, `/help`, `/quit`.

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
  explicit pre-approval. Reading is a higher-risk action than writing into
  the workspace, and we'd rather have the user list trusted roots.

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
- [x] `easy-research clean` subcommand and `.env.example` auto-generation in `init` — 2026-05-14
- [x] `.env` discovery fixed for installed console script (use cwd, not module path) — 2026-05-14
- [x] Tool-schema generator now emits valid `items` for bare `list` annotations — 2026-05-14

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full history.

## Status

Phases 1–7 implemented. The CLI installs as `easy-research` and exposes 21 LLM-callable tools across `code`, `writing`, `project`, and `dynamic` categories.

## License

TBD
