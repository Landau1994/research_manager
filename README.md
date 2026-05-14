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
easy-research init .

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
│   │   ├── writing_tools.py   # list_results, read_text_file, write_report, ...
│   │   └── project_tools.py   # init_project, list_workspace, add_task, ...
│   ├── planner/
│   │   └── task_graph.py      # DAG with cycle detection and topological sort
│   ├── workspace/
│   │   └── manager.py         # init/validate workspace, persist task state
│   ├── context.py             # Current workspace path (shared runtime context)
│   └── cli.py                 # REPL, single-question, batch, init, validate
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
| `CONDA_EXE` | Path to conda executable | (from PATH) |

## CLI

```
easy-research                          # interactive REPL
easy-research "question"               # single question
easy-research -m article "draft intro" # writing mode
easy-research -f question.md           # read from file
easy-research init [path]              # create workspace layout
easy-research validate [path]          # check workspace structure
easy-research batch questions.md -w 4  # parallel batch mode
```

REPL commands: `/tools`, `/mode <kind>`, `/workspace`, `/reset`, `/help`, `/quit`.

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

### Completed
- [x] Define workspace directory structure (`demo_work_dir/`) — 2026-05-14
- [x] Initial README and project design — 2026-05-14
- [x] Phase 1 — Foundation (pyproject.toml, LLM client, tool registry, CLI) — 2026-05-14
- [x] Phase 2 — Code execution (subprocess runner, conda integration) — 2026-05-14
- [x] Phase 3 — Writing pipeline (mode prompts, report tools) — 2026-05-14
- [x] Phase 4 — Project management (workspace, task graph, state) — 2026-05-14
- [x] Phase 5 — Polish (batch mode, examples) — 2026-05-14

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full history.

## Status

All planned phases (1–5) implemented. The CLI installs as `easy-research` and exposes 14 LLM-callable tools across `code`, `writing`, and `project` categories.

## License

TBD
