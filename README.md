# Research Manager

An AI agent that manages research projects end-to-end: executing analysis code, orchestrating LLM calls, and producing publication-ready outputs (paper drafts, book chapters, blog posts).

## Quick Start

```bash
# 1. Install from GitHub
git clone https://github.com/Landau1994/research_manager.git
cd research_manager
pip install -e .

# For development/testing:
# pip install -e ".[dev]"

# Or install directly with pip (no clone):
# pip install git+https://github.com/Landau1994/research_manager.git

# 2. Configure (uses an OpenAI-compatible API; defaults to DeepSeek)
cp .env.example .env
# edit .env and set OPENAI_API_KEY=...

# 3. Optional: configure R support in conda
# Defaults to the current conda env, configures USTC/Westlake R mirrors,
# installs r-base=4.5.3 plus missing build/system deps, then installs tidyverse.
easy-research setup-r

# 4. Create a project workspace
mkdir my_project && cd my_project
easy-research init .   # also drops a .env.example and can optionally configure R

# 5. Drop your data into data/ and your scripts into code/<language>/, then chat:
easy-research
```

Inside the REPL:
```
you > run code/python/analyze.py inside the conda env "data-sci"
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
├── code/              # Analysis and processing scripts
│   ├── python/        # Python scripts and auto-populated requirements.txt
│   ├── r/             # R scripts and setup_packages.R
│   └── bash/          # Shell scripts and setup_packages.sh
├── script/            # Legacy script location, still supported
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
easy-research setup-r                  # configure R/tidyverse in a conda env
easy-research batch questions.md -w 4  # parallel batch mode
easy-research --auto-approve ...       # skip script-proposal prompts
easy-research --record                 # record a trajectory for future RL data
easy-research sessions list            # list saved sessions + trajectories
easy-research sessions trace <id>      # summary of a recorded trajectory
easy-research sessions analyze <id>    # Tier 2 derived signals as JSON
easy-research sessions prune --keep N  # manually trim trajectories
```

REPL commands: `/tools`, `/mode <kind>`, `/workspace`, `/allow <dir>`, `/allowed`, `/deny <dir>`, `/package <name>`, `/env scan|plan`, `/sessions`, `/save [name]`, `/load <name>`, `/branch [name]`, `/remember <fact>`, `/memory`, `/good [note]`, `/bad [note]`, `/outcome <kind>`, `/redo`, `/reset`, `/help`, `/quit`.

### Optional R Setup

After installing the package, run:

```bash
easy-research setup-r
```

When you first run `easy-research init` in a new workspace, the CLI also asks
whether to configure R immediately. The setup helper defaults to the currently
active conda environment (`CONDA_DEFAULT_ENV`). It first checks whether that
environment already has R, the R package `tidyverse`, the configured R package
repositories, and required system/build dependencies. Missing pieces are planned
only when needed.

```bash
# If R is missing:
conda install -n <env> -c conda-forge -y r-base=4.5.3 pkg-config rust libuv curl libcurl

# If the R repositories are not configured:
easy-research configure-r-repos -n <env>

# If the target env is currently active:
Rscript -e "install.packages('tidyverse')"

# If configuring a different env:
conda run -n <env> Rscript -e "install.packages('tidyverse')"
```

You can choose a different environment or only print the planned commands:

```bash
easy-research setup-r --env my-r-env
easy-research setup-r --dry-run
easy-research setup-r --yes
```

### Development

Install the package with test dependencies:

```bash
pip install -e ".[dev]"
python -m pytest -q
```

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

### Attaching files with `@`

Inside any user message you can include `@<path>` tokens to attach files or
directories to the prompt:

```
you > review @code/python/clean.py and compare with @docs/style.md
you > what's in @res/?
you > @~/refs/dataset.csv  ← needs /allow first
```

Behavior:
- Small text files (≤ 50 KB) are inlined verbatim — no extra tool round-trip.
- Large files or non-text extensions become a one-line hint pointing the LLM at
  `read_text_file` / `read_external_file`.
- Directories show a shallow listing (≤ 30 entries) plus a hint to call
  `list_workspace`.
- Paths outside the workspace must be in the external whitelist; otherwise the
  attachment is refused with a `/allow` hint and contents are not shown.
- `name@host.com` style emails are not misinterpreted (the path must resolve to
  an existing file or directory).

**Tab completion.** Inside the REPL, pressing Tab while typing an `@<partial>`
token completes against the workspace (and `~/...`, absolute paths). It
descends into subdirectories — `@code/<TAB>` lists everything under
`code/`, `@code/python/f<TAB>` filters by prefix. Tab on a leading `/` completes
slash commands (`/he<TAB>` → `/help`). Up/Down browses input history; Ctrl-R
does a reverse search. Backspace correctly deletes one character at a time on
CJK input (the REPL uses `prompt_toolkit` for line editing rather than the
default cooked-mode `input()`).

See [examples/quickstart.md](examples/quickstart.md) for a walkthrough.

### Project memory and resuming sessions

The agent has two layers of cross-session memory:

1. **`MEMORY.md`** — a Markdown file in the workspace root, loaded into
   the system prompt every time the REPL starts. Use it for durable
   project facts: conda env names, code conventions, file naming rules,
   "always run X before Y" decisions, anything you'd otherwise re-explain
   on every restart. Edit by hand, or use:
   - `/remember <fact>` — appends a fact under an auto-derived title.
   - `/remember Title :: fact body` — explicit title.
   - `/memory` — show current contents.
   - The agent itself can call the `remember_fact` tool when you say
     things like "remember that we use the `raretools` env for R".
   Sections with the same title deduplicate — subsequent facts under the
   same heading append as dated bullets rather than spawning a new section.

2. **Auto-resume** — at REPL startup, if the freshest auto-save slot has
   user turns and is at most 7 days old, the REPL prompts `resume? (y/N)`
   with a one-line preview of the last user message. Skipped silently
   when stdin isn't a TTY (batch / piped) or `RM_NO_RESUME=1`. You can
   still resume any older slot manually with `/load auto-{0,1,2}`.

REPL command history is also persisted to
`<workspace>/.research_manager_sessions/repl_history` so Up/Down works
across REPL restarts.

## Implemented Feature Areas

- OpenAI-compatible LLM client with tool calling, writing modes, and a
  registry-driven tool schema.
- Isolated Python/R/shell execution with conda support, timeouts, output
  capture, and workspace file-change tracking.
- Structured workspace creation, validation, cleanup, task planning, and result
  organization.
- Dynamic script workflow: propose, preview, execute, save, revise, and rerun
  project-specific Python/R/shell scripts.
- Dependency scanning and environment planning via `/env scan` and `/env plan`.
- Package builder for promoting workspace scripts into pip-installable packages
  under `packages/`.
- Session persistence, project memory, batch mode, external file whitelisting,
  and optional trajectory recording for later analysis.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full history.

## Status

The CLI installs as `easy-research` and supports interactive, one-shot, and
batch workflows. The tool set covers code execution, writing, project
management, dynamic script creation, package building, dependency planning,
memory, external file access, and optional trajectory recording. Article mode
is tuned for Nature/Nature Communications-style scientific writing.

## License

TBD

## Recent Updates

### 2026-07-05

- Added `easy-research setup-r` for conda-only R setup. It detects R,
  `tidyverse`, and build/system dependencies before planning installs, so
  already-installed conda packages are not reinstalled.
- R package repositories are configured in the target conda env's
  `Rprofile.site`: USTC CRAN plus Westlake Bioconductor software and annotation
  mirrors.
- When setup targets the currently active conda env, R package installation uses
  direct `Rscript -e "install.packages(...)"`; `conda run -n <env>` is only used
  for non-active envs.
- Workspace initialization now creates `code/python`, `code/r`, and `code/bash`
  scaffold directories while continuing to support legacy `script/`.
