"""LLM-callable tools for executing scripts in conda environments."""

from __future__ import annotations

import json
import os

from research_manager.context import get_workspace
from research_manager.executor.runner import ScriptRunner
from research_manager.tools.registry import tool


def _runner() -> ScriptRunner:
    timeout = int(os.environ.get("RM_TOOL_TIMEOUT", "300"))
    return ScriptRunner(workspace=get_workspace(), default_timeout=timeout)


@tool(name="run_python", category="code")
def run_python(script: str, env: str, timeout: int) -> str:
    """Run a Python script inside a conda environment.

    Args:
        script: Path to the .py file (relative paths resolve against the workspace, then code/python/, then legacy script/).
        env: Conda environment name. Pass empty string to use the current environment.
        timeout: Wall-clock timeout in seconds.
    """
    result = _runner().run_python(
        script=script,
        env=env or None,
        timeout=timeout,
    )
    return json.dumps(result.summary(), ensure_ascii=False)


@tool(name="run_r", category="code")
def run_r(script: str, env: str, timeout: int) -> str:
    """Run an R script inside a conda environment.

    Args:
        script: Path to the .R/.r file (relative paths resolve against the workspace, then code/r/, then legacy script/).
        env: Conda environment name. Pass empty string to use the current environment.
        timeout: Wall-clock timeout in seconds.
    """
    result = _runner().run_r(
        script=script,
        env=env or None,
        timeout=timeout,
    )
    return json.dumps(result.summary(), ensure_ascii=False)


@tool(name="run_shell", category="code")
def run_shell(command: str, env: str, timeout: int) -> str:
    """Run a shell command in the workspace, optionally inside a conda env.

    Args:
        command: The shell command to run (executed via `bash -lc`).
        env: Conda environment name. Pass empty string to skip activation.
        timeout: Wall-clock timeout in seconds.
    """
    result = _runner().run_shell(
        command=command,
        env=env or None,
        timeout=timeout,
    )
    return json.dumps(result.summary(), ensure_ascii=False)
