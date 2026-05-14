"""Session-level whitelist of directories the agent is allowed to read outside the workspace.

The whitelist is seeded from the `RM_EXTERNAL_READ_PATHS` environment variable
(colon-separated absolute paths) at startup, and can be extended at runtime via
the REPL `/allow` command or `add_allowed_dir()`.

A path P is considered allowed iff some ancestor of P (or P itself, if it is a
file under an allowed directory) is in the set. This is intentionally simple:
no globs, no wildcards, no symlink chasing — the user lists concrete roots.
"""

from __future__ import annotations

import os
from pathlib import Path

_allowed_dirs: set[Path] = set()


def init_from_env() -> None:
    """Seed the whitelist from `RM_EXTERNAL_READ_PATHS` (colon-separated)."""
    raw = os.environ.get("RM_EXTERNAL_READ_PATHS", "").strip()
    if not raw:
        return
    for part in raw.split(":"):
        part = part.strip()
        if part:
            add_allowed_dir(part)


def add_allowed_dir(path: str | Path) -> Path:
    """Register a directory as readable. Returns the resolved path.

    Raises FileNotFoundError if the path does not exist, or NotADirectoryError
    if it is a file.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"path does not exist: {p}")
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {p}")
    _allowed_dirs.add(p)
    return p


def remove_allowed_dir(path: str | Path) -> bool:
    """Drop a directory from the whitelist. Returns True if it was present."""
    p = Path(path).expanduser().resolve()
    if p in _allowed_dirs:
        _allowed_dirs.remove(p)
        return True
    return False


def is_allowed(target: str | Path) -> bool:
    """Check whether `target` (file or dir) is under some allowed directory."""
    t = Path(target).expanduser().resolve()
    for allowed in _allowed_dirs:
        try:
            t.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def allowed_dirs() -> list[str]:
    """Return the current whitelist as sorted absolute paths."""
    return sorted(str(p) for p in _allowed_dirs)


def clear_allowed() -> None:
    """Empty the whitelist (used by tests)."""
    _allowed_dirs.clear()
