"""DAG-based task graph for resolving execution order of research steps."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    description: str
    command: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | completed | failed
    result: dict | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "command": self.command,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            id=data["id"],
            description=data.get("description", ""),
            command=data.get("command", ""),
            depends_on=list(data.get("depends_on", [])),
            status=data.get("status", "pending"),
            result=data.get("result"),
        )


class TaskGraph:
    """A DAG of research tasks. Detects cycles and yields topological order."""

    def __init__(self, tasks: list[Task] | None = None):
        self.tasks: dict[str, Task] = {}
        for t in tasks or []:
            self.add(t)

    def add(self, task: Task) -> None:
        if task.id in self.tasks:
            raise ValueError(f"task id already exists: {task.id}")
        self.tasks[task.id] = task

    def remove(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        for t in self.tasks.values():
            t.depends_on = [d for d in t.depends_on if d != task_id]

    def validate(self) -> None:
        for t in self.tasks.values():
            for dep in t.depends_on:
                if dep not in self.tasks:
                    raise ValueError(f"task {t.id} depends on unknown task {dep}")
        # Cycle check via DFS
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self.tasks}

        def visit(tid: str) -> None:
            if color[tid] == GRAY:
                raise ValueError(f"cycle detected involving task {tid}")
            if color[tid] == BLACK:
                return
            color[tid] = GRAY
            for dep in self.tasks[tid].depends_on:
                visit(dep)
            color[tid] = BLACK

        for tid in self.tasks:
            if color[tid] == WHITE:
                visit(tid)

    def topological_order(self) -> list[str]:
        """Return task ids in dependency order (deps before dependents)."""
        self.validate()
        in_deg = {tid: 0 for tid in self.tasks}
        for t in self.tasks.values():
            for _ in t.depends_on:
                in_deg[t.id] += 1
        # We need to invert: count how many remaining deps each task has
        ready = [tid for tid, d in in_deg.items() if d == 0]
        ready.sort()
        order: list[str] = []
        in_deg_remaining = dict(in_deg)
        # Build reverse map: dep -> [dependents]
        reverse: dict[str, list[str]] = {tid: [] for tid in self.tasks}
        for t in self.tasks.values():
            for dep in t.depends_on:
                reverse[dep].append(t.id)
        while ready:
            current = ready.pop(0)
            order.append(current)
            for dependent in reverse[current]:
                in_deg_remaining[dependent] -= 1
                if in_deg_remaining[dependent] == 0:
                    ready.append(dependent)
            ready.sort()
        if len(order) != len(self.tasks):
            raise ValueError("cycle detected (could not produce full order)")
        return order

    def runnable(self) -> list[str]:
        """Return task ids whose dependencies are all completed and which are pending."""
        out = []
        for t in self.tasks.values():
            if t.status != "pending":
                continue
            if all(self.tasks[d].status == "completed" for d in t.depends_on if d in self.tasks):
                out.append(t.id)
        return sorted(out)

    def to_dict(self) -> dict:
        return {"tasks": [t.to_dict() for t in self.tasks.values()]}

    @classmethod
    def from_dict(cls, data: dict) -> "TaskGraph":
        return cls([Task.from_dict(d) for d in data.get("tasks", [])])
