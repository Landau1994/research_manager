"""Shared runtime context (current workspace path)."""

from __future__ import annotations

import os
from pathlib import Path

_workspace: Path | None = None


def set_workspace(path: str | Path) -> Path:
    global _workspace
    _workspace = Path(path).resolve()
    return _workspace


def get_workspace() -> Path:
    if _workspace is not None:
        return _workspace
    env = os.environ.get("RM_WORKSPACE")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()
