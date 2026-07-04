# Quickstart

This walks through a minimal end-to-end usage of `easy-research`.

## 1. Install

```bash
pip install -e .
cp .env.example .env  # fill in OPENAI_API_KEY
```

## 2. Initialize a workspace

```bash
mkdir my_project && cd my_project
easy-research init .
```

This creates:
```
my_project/
├── data/
├── code/
│   ├── python/
│   ├── r/
│   └── bash/
├── res/{fig,h5ad,python_obj,r_obj,txt}/
├── report/{article,blog,book}/
└── .research_manager_state.json
```

## 3. Add data and a script

Drop a CSV in `data/`, then write `code/python/analyze.py`:
```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("data/input.csv")
summary = df.describe()
summary.to_csv("res/txt/summary.csv")

df["value"].hist()
plt.savefig("res/fig/histogram.png")
```

## 4. Run the agent

```bash
easy-research
```

In the REPL:
```
you > run code/python/analyze.py in conda env "data-sci" and tell me what it produced
you > /mode article
you > draft an introduction for an article based on the summary in res/txt/summary.csv
you > /quit
```

## 5. Batch mode

Create `questions.md`:
```
init a workspace at ./projA and add a task to clean data
---
list the files in res/
```

Run:
```bash
easy-research batch questions.md -w 2
```

## 6. Modes

| Mode    | When to use |
|---------|-------------|
| base    | general execution + planning |
| article | drafting academic papers under `report/article/` |
| blog    | drafting blog posts under `report/blog/` |
| book    | drafting book chapters under `report/book/` |

Switch mid-session with `/mode <kind>` or pass `-m <kind>` on the command line.
