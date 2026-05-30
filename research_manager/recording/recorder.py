"""Trajectory recorder: passive capture of LLM events, tool calls, and workspace snapshots.

Storage layout under <workspace>/.research_manager_sessions/:
    objects/<sha[:2]>/<sha[2:]>     # content-addressed blob store, shared across sessions
    trajectories/<session_id>/
        meta.json                    # model, git commit, mode, started_at, ended_at, ...
        events.jsonl                 # append-only event stream
        snapshots/step_<i>.json      # {relative_path: sha256_or_meta} manifest

Recording is opt-in: a recorder is constructed only when --record / RM_RECORD=1
is set. The client checks `if self.recorder:` before emitting; when disabled,
overhead is one attribute lookup per event point.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SESSIONS_DIR = ".research_manager_sessions"
_TRAJECTORIES_SUBDIR = "trajectories"
_OBJECTS_SUBDIR = "objects"
_SNAPSHOTS_SUBDIR = "snapshots"

_DEFAULT_WATCH_DIRS = ("script", "res", "report")
_DEFAULT_RETENTION_KEEP = 50
_DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024  # skip content hashing above this; record meta only


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def _git_commit(workspace: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _canonical_bytes(obj: Any) -> bytes:
    """Stable JSON encoding for hashing: sorted keys, no whitespace, utf-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


@dataclass
class _FileStat:
    """Cached fingerprint of a workspace file (mtime + size + sha256)."""
    mtime_ns: int
    size: int
    sha256: str


@dataclass
class TrajectoryRecorder:
    workspace: Path
    session_id: str = field(default_factory=_new_session_id)
    model: str = ""
    mode: str = "base"
    prompt_version: str | None = None
    tags: list[str] = field(default_factory=list)
    watch_dirs: tuple[str, ...] = _DEFAULT_WATCH_DIRS
    retention_keep: int = _DEFAULT_RETENTION_KEEP
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES

    # internal state
    _step_idx: int = field(default=0, init=False)
    _started: bool = field(default=False, init=False)
    _file_cache: dict[str, _FileStat] = field(default_factory=dict, init=False)
    _events_fh: Any = field(default=None, init=False)

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------
    @property
    def sessions_root(self) -> Path:
        return self.workspace / _SESSIONS_DIR

    @property
    def session_dir(self) -> Path:
        return self.sessions_root / _TRAJECTORIES_SUBDIR / self.session_id

    @property
    def objects_dir(self) -> Path:
        return self.sessions_root / _OBJECTS_SUBDIR

    @property
    def snapshots_dir(self) -> Path:
        return self.session_dir / _SNAPSHOTS_SUBDIR

    @property
    def events_path(self) -> Path:
        return self.session_dir / "events.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.session_dir / "meta.json"

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self._events_fh = self.events_path.open("a", encoding="utf-8")
        self._write_meta({
            "version": 1,
            "session_id": self.session_id,
            "started_at": _now_iso(),
            "ended_at": None,
            "model": self.model,
            "mode": self.mode,
            "prompt_version": self.prompt_version,
            "tags": list(self.tags),
            "workspace": str(self.workspace),
            "git_commit": _git_commit(self.workspace),
            "watch_dirs": list(self.watch_dirs),
        })
        self._prune_old_trajectories()
        # initial snapshot at step 0 captures the workspace before any tool runs
        self._take_snapshot()
        self._started = True

    def close(self, outcome: str | None = None) -> None:
        if not self._started:
            return
        if self._events_fh is not None:
            try:
                self._events_fh.flush()
                self._events_fh.close()
            except OSError:
                pass
            self._events_fh = None
        self._update_meta({"ended_at": _now_iso(), "outcome": outcome, "n_steps": self._step_idx})
        self._started = False

    def __enter__(self) -> TrajectoryRecorder:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(outcome="error" if exc_type else None)

    # ------------------------------------------------------------------
    # event emitters (called from ResearchLLMClient and friends)
    # ------------------------------------------------------------------
    def on_user_message(self, content: str) -> None:
        self._emit({"type": "user_message", "content": content})

    def on_llm_request(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        tools_count: int,
        extra: dict | None = None,
    ) -> str:
        """Record a request. Returns a request_id used to correlate with the response."""
        req_id = secrets.token_hex(6)
        messages_hash = self._put_json(messages)
        self._emit({
            "type": "llm_request",
            "request_id": req_id,
            "model": model,
            "temperature": temperature,
            "tools_count": tools_count,
            "messages_hash": messages_hash,
            "messages_len": len(messages),
            "extra": extra or {},
        })
        return req_id

    def on_llm_response(
        self,
        request_id: str,
        message: dict,
        finish_reason: str | None,
        usage: dict | None,
        latency_ms: float,
    ) -> None:
        message_hash = self._put_json(message)
        self._emit({
            "type": "llm_response",
            "request_id": request_id,
            "finish_reason": finish_reason,
            "usage": usage,
            "latency_ms": round(latency_ms, 2),
            "message_hash": message_hash,
            "has_text": bool(message.get("content")),
            "n_tool_calls": len(message.get("tool_calls") or []),
        })

    def on_tool_call_start(self, call_id: str, name: str, args: dict) -> int:
        step_idx = self._step_idx
        args_hash = self._put_json(args)
        self._emit({
            "type": "tool_call_start",
            "step": step_idx,
            "call_id": call_id,
            "name": name,
            "args_hash": args_hash,
            "started_at_ms": _now_ms(),
        })
        return step_idx

    def on_tool_call_end(
        self,
        call_id: str,
        name: str,
        result: str,
        duration_ms: float,
        step_idx: int,
        error: str | None = None,
    ) -> None:
        result_hash = self._put_bytes(result.encode("utf-8")) if isinstance(result, str) else self._put_json(result)
        self._emit({
            "type": "tool_call_end",
            "step": step_idx,
            "call_id": call_id,
            "name": name,
            "duration_ms": round(duration_ms, 2),
            "result_hash": result_hash,
            "result_size": len(result) if isinstance(result, (str, bytes)) else None,
            "error": error,
        })
        # snapshot AFTER the tool ran — captures any workspace mutations
        self._step_idx += 1
        self._take_snapshot()

    def on_user_label(self, label: str, target: str | None = None, note: str | None = None) -> None:
        """Tier 3 hook: explicit user feedback (/good /bad /outcome)."""
        self._emit({
            "type": "user_label",
            "label": label,
            "target": target,
            "note": note,
        })

    def on_counterfactual(
        self,
        request_id: str,
        chosen_message_hash: str,
        rejected_message: dict,
        temperature: float,
    ) -> None:
        """Tier 4 hook: store an off-policy alternate completion alongside the chosen one."""
        rejected_hash = self._put_json(rejected_message)
        self._emit({
            "type": "counterfactual",
            "request_id": request_id,
            "chosen_message_hash": chosen_message_hash,
            "rejected_message_hash": rejected_hash,
            "temperature": temperature,
            "has_text": bool(rejected_message.get("content")),
            "n_tool_calls": len(rejected_message.get("tool_calls") or []),
        })

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _emit(self, event: dict) -> None:
        if self._events_fh is None:
            return
        event = {"ts": _now_iso(), **event}
        self._events_fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._events_fh.flush()

    def _put_bytes(self, data: bytes) -> str:
        sha = _sha256_bytes(data)
        path = self.objects_dir / sha[:2] / sha[2:]
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.rename(path)
        return sha

    def _put_json(self, obj: Any) -> str:
        return self._put_bytes(_canonical_bytes(obj))

    def _put_file_content(self, path: Path) -> str:
        sha = _sha256_file(path)
        target = self.objects_dir / sha[:2] / sha[2:]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            with path.open("rb") as src, tmp.open("wb") as dst:
                while True:
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    dst.write(block)
            tmp.rename(target)
        return sha

    def _take_snapshot(self) -> None:
        manifest: dict[str, dict] = {}
        new_cache: dict[str, _FileStat] = {}
        for sub in self.watch_dirs:
            base = self.workspace / sub
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if not p.is_file():
                    continue
                rel = str(p.relative_to(self.workspace))
                try:
                    st = p.stat()
                except OSError:
                    continue
                cached = self._file_cache.get(rel)
                if cached and cached.mtime_ns == st.st_mtime_ns and cached.size == st.st_size:
                    sha = cached.sha256
                elif st.st_size > self.max_file_bytes:
                    manifest[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "skipped": True}
                    new_cache[rel] = _FileStat(st.st_mtime_ns, st.st_size, "")
                    continue
                else:
                    sha = self._put_file_content(p)
                manifest[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha}
                new_cache[rel] = _FileStat(st.st_mtime_ns, st.st_size, sha)
        self._file_cache = new_cache
        snap_path = self.snapshots_dir / f"step_{self._step_idx:04d}.json"
        snap_path.write_text(
            json.dumps({"step": self._step_idx, "files": manifest}, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )

    def _write_meta(self, data: dict) -> None:
        self.meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _update_meta(self, patch: dict) -> None:
        try:
            current = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        current.update(patch)
        self._write_meta(current)

    def _prune_old_trajectories(self) -> None:
        root = self.sessions_root / _TRAJECTORIES_SUBDIR
        if not root.exists():
            return
        dirs = [d for d in root.iterdir() if d.is_dir() and d.name != self.session_id]
        if len(dirs) < self.retention_keep:
            return
        dirs.sort(key=lambda d: d.stat().st_mtime)
        excess = len(dirs) - (self.retention_keep - 1)  # leave room for the new session
        for d in dirs[:max(0, excess)]:
            _rmtree(d)

    # ------------------------------------------------------------------
    # convenience
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, workspace: Path, model: str = "", mode: str = "base") -> TrajectoryRecorder | None:
        """Construct from env vars, or return None if recording is disabled.

        Reads:
            RM_RECORD            — "1" / "true" enables recording.
            RM_RECORD_KEEP       — retention count (default 50).
            RM_RECORD_MAX_FILE   — max file bytes for content hashing (default 100MB).
        """
        flag = os.environ.get("RM_RECORD", "").lower()
        if flag not in ("1", "true", "yes", "on"):
            return None
        keep = int(os.environ.get("RM_RECORD_KEEP", _DEFAULT_RETENTION_KEEP))
        max_file = int(os.environ.get("RM_RECORD_MAX_FILE", _DEFAULT_MAX_FILE_BYTES))
        return cls(
            workspace=workspace,
            model=model,
            mode=mode,
            retention_keep=keep,
            max_file_bytes=max_file,
        )


def _rmtree(path: Path) -> None:
    """Best-effort recursive delete; never raise."""
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            try:
                child.unlink()
            except OSError:
                pass
    try:
        path.rmdir()
    except OSError:
        pass


# ----------------------------------------------------------------------
# Module-level active-recorder hook
# ----------------------------------------------------------------------
# The LLM client and the script runner live in different layers; the runner
# does not see the client. Rather than threading a recorder through every
# tool's call signature, we expose a process-wide hook. The client sets it
# at session start; tools that want to emit fine-grained events (e.g.
# subprocess_exit from ScriptRunner) read it via get_active_recorder().
_active_recorder: TrajectoryRecorder | None = None


def set_active_recorder(rec: TrajectoryRecorder | None) -> None:
    global _active_recorder
    _active_recorder = rec


def get_active_recorder() -> TrajectoryRecorder | None:
    return _active_recorder


def emit_subprocess_exit(
    *,
    command: list[str],
    returncode: int,
    timed_out: bool,
    duration_ms: float,
    stdout: str,
    stderr: str,
) -> None:
    """Convenience used by ScriptRunner. No-op when no active recorder."""
    rec = get_active_recorder()
    if rec is None:
        return
    try:
        stdout_hash = rec._put_bytes(stdout.encode("utf-8")) if stdout else None
        stderr_hash = rec._put_bytes(stderr.encode("utf-8")) if stderr else None
    except Exception:
        stdout_hash = stderr_hash = None
    try:
        rec._emit({
            "type": "subprocess_exit",
            "command": command,
            "returncode": returncode,
            "timed_out": timed_out,
            "duration_ms": round(duration_ms, 2),
            "stdout_hash": stdout_hash,
            "stderr_hash": stderr_hash,
            "stdout_size": len(stdout),
            "stderr_size": len(stderr),
        })
    except Exception:
        pass
