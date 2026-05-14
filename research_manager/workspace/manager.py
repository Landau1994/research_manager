"""Initialize and validate the standard research workspace layout."""

from __future__ import annotations

import json
from pathlib import Path

WORKSPACE_DIRS = ["data", "script", "res", "report"]
RES_SUBDIRS = ["fig", "h5ad", "python_obj", "r_obj", "txt"]
REPORT_KINDS = ["article", "blog", "book"]

_STATE_FILENAME = ".research_manager_state.json"

_ENV_EXAMPLE_CONTENT = """\
# Copy this file to .env and fill in your values

# Required
OPENAI_API_KEY=your-api-key-here

# Optional: API endpoint (defaults to DeepSeek)
# OPENAI_BASE_URL=https://api.deepseek.com/beta

# Optional: model name (defaults to deepseek-v4-pro)
# RM_MODEL=deepseek-v4-pro

# Optional: tuning
# RM_MAX_TOKENS=8192
# RM_MAX_ITERATIONS=30
# RM_TEMPERATURE=0.0
# RM_REASONING_EFFORT=high
# RM_TOOL_TIMEOUT=300
"""


def init_workspace(path: str | Path, force: bool = False) -> dict:
    """Create the standard directory structure at `path`.

    Returns a summary describing which directories were created vs. already present.
    """
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    existed: list[str] = []

    for sub in WORKSPACE_DIRS:
        d = root / sub
        (created if not d.exists() else existed).append(sub)
        d.mkdir(parents=True, exist_ok=True)
    for sub in RES_SUBDIRS:
        d = root / "res" / sub
        rel = f"res/{sub}"
        (created if not d.exists() else existed).append(rel)
        d.mkdir(parents=True, exist_ok=True)
    for sub in REPORT_KINDS:
        d = root / "report" / sub
        rel = f"report/{sub}"
        (created if not d.exists() else existed).append(rel)
        d.mkdir(parents=True, exist_ok=True)

    state_path = root / _STATE_FILENAME
    if not state_path.exists() or force:
        state_path.write_text(json.dumps({"version": 1, "tasks": []}, indent=2), encoding="utf-8")

    env_file = root / ".env"
    env_example = root / ".env.example"
    if not env_file.exists() and not env_example.exists():
        env_example.write_text(_ENV_EXAMPLE_CONTENT, encoding="utf-8")
        created.append(".env.example")

    return {
        "workspace": str(root),
        "created": created,
        "existed": existed,
        "state_file": str(state_path.relative_to(root)),
    }


def validate_workspace(path: str | Path) -> dict:
    """Check that all expected directories exist under `path`."""
    root = Path(path).resolve()
    missing: list[str] = []
    for sub in WORKSPACE_DIRS:
        if not (root / sub).is_dir():
            missing.append(sub)
    for sub in RES_SUBDIRS:
        if not (root / "res" / sub).is_dir():
            missing.append(f"res/{sub}")
    for sub in REPORT_KINDS:
        if not (root / "report" / sub).is_dir():
            missing.append(f"report/{sub}")
    return {
        "workspace": str(root),
        "ok": not missing,
        "missing": missing,
        "has_state_file": (root / _STATE_FILENAME).exists(),
    }


def state_path(workspace: str | Path) -> Path:
    return Path(workspace).resolve() / _STATE_FILENAME


def load_state(workspace: str | Path) -> dict:
    sp = state_path(workspace)
    if not sp.exists():
        return {"version": 1, "tasks": []}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "tasks": []}


def save_state(workspace: str | Path, state: dict) -> None:
    sp = state_path(workspace)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
