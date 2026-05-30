"""Tier 2 — derived behavior signals from a recorded trajectory.

All work is offline: read events.jsonl + snapshots and emit a JSON report.
No runtime cost during recording.

Signals produced:
- user_edits_after_agent: files the agent wrote that the user later changed.
- file_survival: agent-written files alive at session end vs overwritten/deleted.
- rerun_pattern: same tool + same args called twice (likely first try failed).
- citation_graph: which later tool_call args reference earlier tool outputs by path.
- intent_signals: lightweight reject/accept tags on user messages following an
  assistant turn (matches words like 不对, 重做, instead, but, redo, wrong).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

# crude but cheap — high precision low recall is what we want for a default tag.
_REJECT_PATTERNS = re.compile(
    r"(?:^|\s)(?:不对|重做|不要这样|不是这|应该改|redo|wrong|that's not|instead|"
    r"don't do|do not do|undo|revert|rollback)(?:\s|$|[,.，。!?])",
    re.IGNORECASE,
)
_ACCEPT_PATTERNS = re.compile(
    r"(?:^|\s)(?:对的|很好|继续|完美|可以|good|nice|perfect|great|exactly|continue|keep going)(?:\s|$|[,.，。!?])",
    re.IGNORECASE,
)


def _load_events(traj_dir: Path) -> list[dict]:
    p = traj_dir / "events.jsonl"
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _load_snapshots(traj_dir: Path) -> list[dict]:
    snap_dir = traj_dir / "snapshots"
    if not snap_dir.exists():
        return []
    out = []
    for p in sorted(snap_dir.glob("step_*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _file_diffs(snapshots: list[dict]) -> list[dict]:
    """Per-step diff: {step, added, modified, deleted, hashes_after}."""
    diffs: list[dict] = []
    prev: dict[str, str] = {}
    for snap in snapshots:
        files = snap.get("files", {})
        cur = {p: meta.get("sha256", "") for p, meta in files.items()}
        added = sorted(p for p in cur if p not in prev)
        modified = sorted(p for p in cur if p in prev and cur[p] != prev[p])
        deleted = sorted(p for p in prev if p not in cur)
        diffs.append({
            "step": snap.get("step", -1),
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "hashes_after": cur,
        })
        prev = cur
    return diffs


def _read_object(traj_dir: Path, sha: str) -> bytes | None:
    obj = traj_dir.parent.parent / "objects" / sha[:2] / sha[2:]
    if obj.exists():
        return obj.read_bytes()
    return None


def _user_edits_after_agent(events: list[dict], diffs: list[dict]) -> list[dict]:
    """Files written by an agent tool_call and later changed (next tool's diff).

    Heuristic only — we cannot truly attribute a change to the user vs
    a follow-up agent step without timestamps + UI signals; we report the
    case where the same file appears in 'modified' across consecutive steps.
    """
    # Map step -> set of files added/modified by tool at that step
    step_changes: dict[int, set[str]] = {}
    for d in diffs:
        step_changes[d["step"]] = set(d["added"]) | set(d["modified"])
    out: list[dict] = []
    seen_first: dict[str, int] = {}
    for d in diffs:
        for path in d["modified"]:
            if path in seen_first:
                out.append({"path": path, "first_step": seen_first[path], "later_step": d["step"]})
            seen_first.setdefault(path, d["step"])
        for path in d["added"]:
            seen_first.setdefault(path, d["step"])
    return out


def _file_survival(diffs: list[dict]) -> dict[str, Any]:
    if not diffs:
        return {"created": 0, "alive_at_end": 0, "survival_rate": None}
    final = diffs[-1]["hashes_after"]
    created_paths: set[str] = set()
    deleted_or_overwritten: set[str] = set()
    for d in diffs:
        for p in d["added"]:
            created_paths.add(p)
        for p in d["deleted"]:
            deleted_or_overwritten.add(p)
        for p in d["modified"]:
            deleted_or_overwritten.add(p)
    alive = created_paths & set(final.keys())
    return {
        "created": len(created_paths),
        "alive_at_end": len(alive),
        "survival_rate": (len(alive) / len(created_paths)) if created_paths else None,
        "alive_files": sorted(alive),
    }


def _rerun_pattern(events: list[dict]) -> list[dict]:
    """Find tool_call_start events with identical (name, args_hash) appearing twice."""
    seen: dict[tuple[str, str], int] = {}
    reruns: list[dict] = []
    for ev in events:
        if ev.get("type") != "tool_call_start":
            continue
        key = (ev.get("name", ""), ev.get("args_hash", ""))
        if key in seen:
            reruns.append({
                "tool": key[0],
                "args_hash": key[1],
                "first_step": seen[key],
                "rerun_step": ev.get("step", -1),
            })
        else:
            seen[key] = ev.get("step", -1)
    return reruns


def _citation_graph(events: list[dict], traj_dir: Path) -> list[dict]:
    """Walk tool_call args; flag string args that match a path produced by an earlier tool."""
    produced_paths: list[tuple[int, str]] = []  # (step, path)
    citations: list[dict] = []
    args_cache: dict[int, dict] = {}

    for ev in events:
        if ev.get("type") == "tool_call_start":
            sha = ev.get("args_hash", "")
            data = _read_object(traj_dir, sha)
            if data is None:
                continue
            try:
                args = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            args_cache[ev.get("step", -1)] = args
            step = ev.get("step", -1)
            # cite earlier outputs
            for v in _walk_strings(args):
                for prev_step, path in produced_paths:
                    if prev_step >= step:
                        continue
                    if path in v or v.endswith(path):
                        citations.append({
                            "from_step": step,
                            "to_step": prev_step,
                            "tool": ev.get("name", ""),
                            "referenced_path": path,
                        })

        if ev.get("type") == "tool_call_end":
            # we don't have direct file outputs at the event level — rely on snapshots
            pass

    return citations


def _walk_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def _intent_signals(events: list[dict]) -> list[dict]:
    """Tag user messages that follow an assistant turn with reject/accept markers."""
    out: list[dict] = []
    last_assistant_seen = False
    for ev in events:
        t = ev.get("type")
        if t == "llm_response":
            last_assistant_seen = True
            continue
        if t == "user_message" and last_assistant_seen:
            content = ev.get("content", "") or ""
            tag = None
            if _REJECT_PATTERNS.search(content):
                tag = "reject"
            elif _ACCEPT_PATTERNS.search(content):
                tag = "accept"
            out.append({
                "ts": ev.get("ts", ""),
                "tag": tag,
                "content_len": len(content),
            })
            last_assistant_seen = False
    return out


def analyze_trajectory(traj_dir: Path) -> dict:
    """Run all Tier 2 analyses on a trajectory directory and return a JSON-able report."""
    events = _load_events(traj_dir)
    snapshots = _load_snapshots(traj_dir)
    diffs = _file_diffs(snapshots)

    # tool usage histogram
    tool_counts = Counter(ev.get("name", "") for ev in events if ev.get("type") == "tool_call_end")

    # counterfactual count (Tier 4)
    cf_count = sum(1 for ev in events if ev.get("type") == "counterfactual")

    return {
        "trajectory": str(traj_dir.name),
        "n_events": len(events),
        "n_snapshots": len(snapshots),
        "tool_call_counts": dict(tool_counts),
        "counterfactuals_recorded": cf_count,
        "user_edits_after_agent": _user_edits_after_agent(events, diffs),
        "file_survival": _file_survival(diffs),
        "rerun_pattern": _rerun_pattern(events),
        "citation_graph": _citation_graph(events, traj_dir),
        "intent_signals": _intent_signals(events),
    }
