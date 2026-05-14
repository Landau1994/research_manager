"""LLM-callable tools for workspace and task-graph management."""

from __future__ import annotations

import json
from pathlib import Path

from research_manager.context import get_workspace
from research_manager.planner.task_graph import Task, TaskGraph
from research_manager.tools.registry import tool
from research_manager.workspace.manager import (
    init_workspace,
    load_state,
    save_state,
    validate_workspace,
)


def _safe_under(workspace: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


@tool(name="init_project", category="project")
def init_project_tool(path: str, force: bool) -> str:
    """Create the standard research workspace layout at `path`.

    Args:
        path: Directory where the workspace should be created (relative paths resolve against the current workspace).
        force: If true, reset the state file even if it already exists.
    """
    base = get_workspace()
    target = (base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    result = init_workspace(target, force=force)
    return json.dumps(result, ensure_ascii=False)


@tool(name="validate_project", category="project")
def validate_project_tool(path: str) -> str:
    """Check that a workspace has all expected directories.

    Args:
        path: Workspace path. Pass empty string to validate the current workspace.
    """
    base = get_workspace()
    target = base if not path else ((base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve())
    return json.dumps(validate_workspace(target), ensure_ascii=False)


@tool(name="list_workspace", category="project")
def list_workspace_tool(subdir: str, max_files: int) -> str:
    """List files in the current workspace under a subdirectory.

    Args:
        subdir: Subdirectory (e.g. "data", "script", "res/fig"). Pass empty string for the workspace root.
        max_files: Maximum number of files to return.
    """
    ws = get_workspace()
    base = ws / subdir if subdir else ws
    if not _safe_under(ws, base):
        return json.dumps({"error": "path escapes workspace"}, ensure_ascii=False)
    if not base.exists():
        return json.dumps({"error": f"{subdir or '.'} does not exist"}, ensure_ascii=False)
    files: list[dict] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith(".research_manager"):
            continue
        files.append({
            "path": str(p.relative_to(ws)),
            "size_bytes": p.stat().st_size,
        })
        if len(files) >= max_files:
            break
    return json.dumps({"workspace": str(ws), "subdir": subdir, "files": files}, ensure_ascii=False)


@tool(name="add_task", category="project")
def add_task_tool(task_id: str, description: str, command: str, depends_on: list) -> str:
    """Add a task to the project task graph.

    Args:
        task_id: Unique identifier.
        description: Human-readable description.
        command: A short string describing the command/script (e.g. "run_python script/clean.py").
        depends_on: List of task ids that must complete before this one.
    """
    ws = get_workspace()
    state = load_state(ws)
    graph = TaskGraph.from_dict({"tasks": state.get("tasks", [])})
    if task_id in graph.tasks:
        return json.dumps({"error": f"task {task_id} already exists"}, ensure_ascii=False)
    graph.add(Task(id=task_id, description=description, command=command, depends_on=list(depends_on or [])))
    try:
        graph.validate()
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    state["tasks"] = graph.to_dict()["tasks"]
    save_state(ws, state)
    return json.dumps({"ok": True, "task_id": task_id}, ensure_ascii=False)


@tool(name="list_tasks", category="project")
def list_tasks_tool(status_filter: str) -> str:
    """List tasks in the project task graph, optionally filtered by status.

    Args:
        status_filter: One of "pending", "running", "completed", "failed", or empty string for all.
    """
    ws = get_workspace()
    state = load_state(ws)
    graph = TaskGraph.from_dict({"tasks": state.get("tasks", [])})
    try:
        order = graph.topological_order()
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    rows = []
    for tid in order:
        t = graph.tasks[tid]
        if status_filter and t.status != status_filter:
            continue
        rows.append(t.to_dict())
    return json.dumps({"tasks": rows, "runnable_now": graph.runnable()}, ensure_ascii=False)


@tool(name="update_task_status", category="project")
def update_task_status_tool(task_id: str, status: str) -> str:
    """Update the status of a task in the project task graph.

    Args:
        task_id: The task id.
        status: One of "pending", "running", "completed", "failed".
    """
    valid = {"pending", "running", "completed", "failed"}
    if status not in valid:
        return json.dumps({"error": f"status must be one of {sorted(valid)}"}, ensure_ascii=False)
    ws = get_workspace()
    state = load_state(ws)
    graph = TaskGraph.from_dict({"tasks": state.get("tasks", [])})
    if task_id not in graph.tasks:
        return json.dumps({"error": f"unknown task: {task_id}"}, ensure_ascii=False)
    graph.tasks[task_id].status = status
    state["tasks"] = graph.to_dict()["tasks"]
    save_state(ws, state)
    return json.dumps({"ok": True, "task_id": task_id, "status": status}, ensure_ascii=False)
