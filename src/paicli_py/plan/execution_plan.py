from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PlanCycleError(RuntimeError):
    pass


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    description: str
    type: str = "command"
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""

    def is_executable(self, tasks: dict[str, "Task"]) -> bool:
        return (
            self.status == TaskStatus.PENDING
            and all(tasks[dep].status == TaskStatus.COMPLETED for dep in self.dependencies if dep in tasks)
        )


class ExecutionPlan:
    def __init__(self, goal: str):
        self.goal = goal
        self.tasks: dict[str, Task] = {}

    def add_task(self, task: Task) -> None:
        self.tasks[task.id] = task

    def execution_order(self) -> list[str]:
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise PlanCycleError(f"cycle detected at {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            task = self.tasks[task_id]
            for dep in task.dependencies:
                if dep in self.tasks:
                    visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)
            order.append(task_id)

        for task_id in self.tasks:
            visit(task_id)
        return order

    def executable_tasks(self) -> list[Task]:
        return [task for task in self.tasks.values() if task.is_executable(self.tasks)]

    def execution_batches(self) -> list[list[Task]]:
        clone = {
            task_id: Task(task.id, task.description, task.type, list(task.dependencies), task.status, task.result)
            for task_id, task in self.tasks.items()
        }
        batches: list[list[Task]] = []
        while True:
            ready = [
                task for task in clone.values()
                if task.status == TaskStatus.PENDING
                and all(clone[dep].status == TaskStatus.COMPLETED for dep in task.dependencies if dep in clone)
            ]
            if not ready:
                break
            batches.append(ready)
            for task in ready:
                task.status = TaskStatus.COMPLETED
        if any(task.status == TaskStatus.PENDING for task in clone.values()):
            self.execution_order()
        return batches

