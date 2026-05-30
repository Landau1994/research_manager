"""Subprocess script runner with conda environment activation and file tracking."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    command: list[str]
    new_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def summary(self, stdout_max: int = 2000, stderr_max: int = 1000) -> dict:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "duration_seconds": round(self.duration_seconds, 3),
            "timed_out": self.timed_out,
            "command": " ".join(self.command),
            "stdout": _truncate(self.stdout, stdout_max),
            "stderr": _truncate(self.stderr, stderr_max),
            "new_files": self.new_files[:50],
            "modified_files": self.modified_files[:50],
        }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} chars omitted]"


def _conda_executable() -> str | None:
    """Locate conda — env var first, then PATH."""
    exe = os.environ.get("CONDA_EXE")
    if exe and Path(exe).exists():
        return exe
    return shutil.which("conda") or shutil.which("mamba")


def _snapshot_dir(root: Path, watch_dirs: list[str]) -> dict[str, float]:
    """Map path → mtime for files under each watched subdirectory."""
    snapshot: dict[str, float] = {}
    for sub in watch_dirs:
        base = root / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                try:
                    snapshot[str(p)] = p.stat().st_mtime
                except OSError:
                    continue
    return snapshot


def _diff_snapshots(
    before: dict[str, float],
    after: dict[str, float],
) -> tuple[list[str], list[str]]:
    new_files = sorted(p for p in after if p not in before)
    modified = sorted(
        p for p in after
        if p in before and after[p] > before[p]
    )
    return new_files, modified


class ScriptRunner:
    """Run scripts (Python/R/shell) in an isolated subprocess.

    Optionally activates a conda environment, tracks files written under
    `res/` and `report/`, and enforces a wall-clock timeout.
    """

    def __init__(
        self,
        workspace: str | Path,
        default_timeout: int = 300,
        watch_dirs: tuple[str, ...] = ("res", "report"),
    ):
        self.workspace = Path(workspace).resolve()
        self.default_timeout = default_timeout
        self.watch_dirs = list(watch_dirs)

    def run_python(
        self,
        script: str,
        env: str | None = None,
        args: list[str] | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        cmd = self._build_python_cmd(script, env=env, args=args or [])
        return self._run(cmd, timeout=timeout)

    def run_r(
        self,
        script: str,
        env: str | None = None,
        args: list[str] | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        cmd = self._build_r_cmd(script, env=env, args=args or [])
        return self._run(cmd, timeout=timeout)

    def run_shell(
        self,
        command: str,
        env: str | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        if env:
            conda = _conda_executable()
            if conda is None:
                raise RuntimeError("conda not found on PATH; cannot activate env")
            cmd = [conda, "run", "-n", env, "--no-capture-output", "bash", "-lc", command]
        else:
            cmd = ["bash", "-lc", command]
        return self._run(cmd, timeout=timeout)

    def _build_python_cmd(self, script: str, env: str | None, args: list[str]) -> list[str]:
        script_path = self._resolve_script(script)
        if env:
            conda = _conda_executable()
            if conda is None:
                raise RuntimeError("conda not found on PATH; cannot activate env")
            return [conda, "run", "-n", env, "--no-capture-output", "python", str(script_path), *args]
        return ["python", str(script_path), *args]

    def _build_r_cmd(self, script: str, env: str | None, args: list[str]) -> list[str]:
        script_path = self._resolve_script(script)
        if env:
            conda = _conda_executable()
            if conda is None:
                raise RuntimeError("conda not found on PATH; cannot activate env")
            return [conda, "run", "-n", env, "--no-capture-output", "Rscript", str(script_path), *args]
        return ["Rscript", str(script_path), *args]

    def _resolve_script(self, script: str) -> Path:
        p = Path(script)
        if not p.is_absolute():
            # Try as-is (relative to workspace), then under script/
            candidates = [self.workspace / p, self.workspace / "script" / p]
            for c in candidates:
                if c.exists():
                    return c
            return self.workspace / p
        return p

    def _run(self, cmd: list[str], timeout: int | None) -> ExecutionResult:
        deadline = timeout if timeout is not None else self.default_timeout
        before = _snapshot_dir(self.workspace, self.watch_dirs)
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=deadline)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_group(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""
                returncode = proc.returncode if proc.returncode is not None else -1
        except FileNotFoundError as e:
            return ExecutionResult(
                returncode=127,
                stdout="",
                stderr=f"executable not found: {e}",
                duration_seconds=0.0,
                timed_out=False,
                command=cmd,
            )

        duration = time.monotonic() - start
        after = _snapshot_dir(self.workspace, self.watch_dirs)
        new_files, modified_files = _diff_snapshots(before, after)
        try:
            from research_manager.recording import get_active_recorder
            if get_active_recorder() is not None:
                from research_manager.recording.recorder import emit_subprocess_exit
                emit_subprocess_exit(
                    command=cmd,
                    returncode=returncode,
                    timed_out=timed_out,
                    duration_ms=duration * 1000.0,
                    stdout=stdout or "",
                    stderr=stderr or "",
                )
        except Exception:
            pass
        return ExecutionResult(
            returncode=returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            duration_seconds=duration,
            timed_out=timed_out,
            command=cmd,
            new_files=new_files,
            modified_files=modified_files,
        )


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
