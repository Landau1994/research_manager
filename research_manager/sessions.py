"""Session persistence: auto-save (3 rotating slots) and manual save/load.

Storage layout under <workspace>/.research_manager_sessions/:
    auto/
        slot_0.json   # oldest
        slot_1.json
        slot_2.json
    saved/
        <name>.json   # user-named, never auto-deleted

Each JSON file stores:
{
    "version": 1,
    "created_at": <iso>,
    "updated_at": <iso>,
    "model": <str>,
    "mode": <str>,
    "messages": [...]   # the full messages list from ResearchLLMClient
}
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

_SESSIONS_DIR = ".research_manager_sessions"
_AUTO_DIR = "auto"
_SAVED_DIR = "saved"
_NUM_SLOTS = 3


def _sessions_root(workspace: Path) -> Path:
    return workspace / _SESSIONS_DIR


def _auto_dir(workspace: Path) -> Path:
    d = _sessions_root(workspace) / _AUTO_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _saved_dir(workspace: Path) -> Path:
    d = _sessions_root(workspace) / _SAVED_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slot_path(workspace: Path, slot: int) -> Path:
    return _auto_dir(workspace) / f"slot_{slot}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_session_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_session_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_auto_slot(workspace: Path) -> int:
    """Choose the auto-save slot for this session: the oldest or first empty."""
    oldest_slot = 0
    oldest_time = float("inf")
    for i in range(_NUM_SLOTS):
        sp = _slot_path(workspace, i)
        data = _read_session_file(sp)
        if data is None:
            return i
        updated = data.get("updated_at", "")
        try:
            t = datetime.fromisoformat(updated).timestamp()
        except (ValueError, TypeError):
            t = 0.0
        if t < oldest_time:
            oldest_time = t
            oldest_slot = i
    return oldest_slot


def auto_save(
    workspace: Path,
    slot: int,
    messages: list[dict],
    model: str = "",
    mode: str = "base",
) -> Path:
    """Write the current conversation to the given auto-save slot."""
    sp = _slot_path(workspace, slot)
    existing = _read_session_file(sp)
    created = existing.get("created_at", _now_iso()) if existing else _now_iso()
    data = {
        "version": 1,
        "created_at": created,
        "updated_at": _now_iso(),
        "model": model,
        "mode": mode,
        "messages": messages,
    }
    _write_session_file(sp, data)
    return sp


def manual_save(
    workspace: Path,
    name: str,
    messages: list[dict],
    model: str = "",
    mode: str = "base",
) -> Path:
    """Save the conversation under saved/<name>.json (never auto-deleted)."""
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    if not safe_name:
        safe_name = f"session_{int(time.time())}"
    sp = _saved_dir(workspace) / f"{safe_name}.json"
    data = {
        "version": 1,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "model": model,
        "mode": mode,
        "messages": messages,
    }
    _write_session_file(sp, data)
    return sp


def load_session(workspace: Path, name: str) -> dict | None:
    """Load a session by name. Accepts 'auto-0', 'auto-1', 'auto-2' or a saved name."""
    if name.startswith("auto-"):
        try:
            slot = int(name.split("-", 1)[1])
        except (ValueError, IndexError):
            return None
        return _read_session_file(_slot_path(workspace, slot))
    sp = _saved_dir(workspace) / f"{name}.json"
    if not sp.exists():
        sp = _saved_dir(workspace) / name
    return _read_session_file(sp)


def list_sessions(workspace: Path) -> dict:
    """Return a summary of all available sessions (auto + saved)."""
    auto_slots: list[dict | None] = []
    for i in range(_NUM_SLOTS):
        data = _read_session_file(_slot_path(workspace, i))
        if data:
            auto_slots.append({
                "slot": f"auto-{i}",
                "updated_at": data.get("updated_at", ""),
                "model": data.get("model", ""),
                "mode": data.get("mode", ""),
                "turns": len([m for m in data.get("messages", []) if m.get("role") == "user"]),
            })
        else:
            auto_slots.append(None)

    saved: list[dict] = []
    sd = _saved_dir(workspace)
    if sd.exists():
        for f in sorted(sd.glob("*.json")):
            data = _read_session_file(f)
            if data:
                saved.append({
                    "name": f.stem,
                    "updated_at": data.get("updated_at", ""),
                    "model": data.get("model", ""),
                    "mode": data.get("mode", ""),
                    "turns": len([m for m in data.get("messages", []) if m.get("role") == "user"]),
                })

    return {"auto": auto_slots, "saved": saved}
