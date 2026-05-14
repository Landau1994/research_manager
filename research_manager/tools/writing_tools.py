"""LLM-callable tools for drafting and managing report documents."""

from __future__ import annotations

import json
from pathlib import Path

from research_manager.context import get_workspace
from research_manager.tools.registry import tool

_REPORT_KINDS = {"article", "blog", "book"}
_TEXT_EXT = {".md", ".txt", ".rst", ".tex", ".csv", ".tsv", ".log", ".json", ".yaml", ".yml", ".py", ".r", ".R", ".sh"}


def _safe_under(workspace: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


@tool(name="list_results", category="writing")
def list_results(subdir: str) -> str:
    """List files in `res/` (optionally a subdirectory like `fig` or `txt`).

    Args:
        subdir: Subdirectory under `res/` (e.g. "fig", "txt"). Pass empty string for the whole `res/` tree.
    """
    ws = get_workspace()
    base = ws / "res" / subdir if subdir else ws / "res"
    if not base.exists():
        return json.dumps({"error": f"{base.relative_to(ws)} does not exist"}, ensure_ascii=False)
    entries = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            entries.append({
                "path": str(p.relative_to(ws)),
                "size_bytes": p.stat().st_size,
            })
    return json.dumps({"workspace": str(ws), "count": len(entries), "files": entries[:500]}, ensure_ascii=False)


@tool(name="read_text_file", category="writing")
def read_text_file(path: str, max_chars: int) -> str:
    """Read a text file from the workspace.

    Args:
        path: File path relative to the workspace (or absolute, must still be inside workspace).
        max_chars: Maximum characters to return (truncated with notice if exceeded).
    """
    ws = get_workspace()
    target = (ws / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if not _safe_under(ws, target):
        return json.dumps({"error": "path escapes workspace"}, ensure_ascii=False)
    if not target.exists():
        return json.dumps({"error": f"file not found: {path}"}, ensure_ascii=False)
    if target.suffix.lower() not in _TEXT_EXT and target.suffix != "":
        return json.dumps({"error": f"refusing to read non-text extension {target.suffix}"}, ensure_ascii=False)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return json.dumps({"path": str(target.relative_to(ws)), "content": text, "truncated": truncated}, ensure_ascii=False)


@tool(name="write_report", category="writing")
def write_report(kind: str, filename: str, content: str) -> str:
    """Write a draft document under `report/<kind>/`.

    Args:
        kind: One of "article", "blog", "book".
        filename: File name (e.g. "draft.md"). Subdirectories under the kind are allowed.
        content: Full file contents to write (overwrites existing).
    """
    if kind not in _REPORT_KINDS:
        return json.dumps({"error": f"kind must be one of {sorted(_REPORT_KINDS)}"}, ensure_ascii=False)
    ws = get_workspace()
    target = (ws / "report" / kind / filename).resolve()
    if not _safe_under(ws, target):
        return json.dumps({"error": "path escapes workspace"}, ensure_ascii=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps({
        "path": str(target.relative_to(ws)),
        "bytes_written": len(content.encode("utf-8")),
    }, ensure_ascii=False)


@tool(name="append_report", category="writing")
def append_report(kind: str, filename: str, content: str) -> str:
    """Append text to a draft under `report/<kind>/` (creates if missing).

    Args:
        kind: One of "article", "blog", "book".
        filename: File name.
        content: Text to append.
    """
    if kind not in _REPORT_KINDS:
        return json.dumps({"error": f"kind must be one of {sorted(_REPORT_KINDS)}"}, ensure_ascii=False)
    ws = get_workspace()
    target = (ws / "report" / kind / filename).resolve()
    if not _safe_under(ws, target):
        return json.dumps({"error": "path escapes workspace"}, ensure_ascii=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(content)
    return json.dumps({"path": str(target.relative_to(ws)), "bytes_appended": len(content.encode("utf-8"))}, ensure_ascii=False)


@tool(name="list_reports", category="writing")
def list_reports(kind: str) -> str:
    """List existing drafts under `report/<kind>/`.

    Args:
        kind: One of "article", "blog", "book". Pass empty string to list all kinds.
    """
    ws = get_workspace()
    kinds = [kind] if kind else sorted(_REPORT_KINDS)
    if kind and kind not in _REPORT_KINDS:
        return json.dumps({"error": f"kind must be one of {sorted(_REPORT_KINDS)} or empty"}, ensure_ascii=False)
    out = {}
    for k in kinds:
        base = ws / "report" / k
        if not base.exists():
            out[k] = []
            continue
        out[k] = [str(p.relative_to(ws)) for p in sorted(base.rglob("*")) if p.is_file()]
    return json.dumps({"workspace": str(ws), "reports": out}, ensure_ascii=False)
