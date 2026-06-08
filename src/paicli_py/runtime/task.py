from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable


class TaskStatus(str, Enum):
    ENQUEUED = "enqueued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @classmethod
    def from_value(cls, value: str | None) -> "TaskStatus":
        if not value:
            return cls.ENQUEUED
        normalized = value.lower()
        for status in cls:
            if normalized in {status.value, status.name.lower()}:
                return status
        return cls.ENQUEUED


@dataclass(frozen=True)
class DurableTask:
    id: str
    status: TaskStatus
    prompt: str
    result: str = ""
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0

    @property
    def terminal(self) -> bool:
        return self.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}

    def short_prompt(self, max_chars: int = 80) -> str:
        normalized = self.prompt.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ").strip()
        return normalized if len(normalized) <= max_chars else normalized[:max_chars] + "..."


class DurableTaskManager:
    def __init__(self, db_path: str | Path, runner: Callable[[str], str], worker_count: int = 2):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.worker_count = max(1, worker_count)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._condition = threading.Condition(threading.RLock())
        self._running = False
        self._workers: list[threading.Thread] = []
        self._running_tasks: dict[str, threading.Thread] = {}
        self._init_tables()
        self._recover_running_tasks()

    @classmethod
    def open_default(cls, runner: Callable[[str], str]) -> "DurableTaskManager":
        return cls(default_task_db_path(), runner, _worker_count())

    def start(self) -> None:
        with self._condition:
            if self._running:
                return
            self._running = True
            for index in range(self.worker_count):
                worker = threading.Thread(target=self._worker_loop, name=f"paicli-task-worker-{index}", daemon=True)
                self._workers.append(worker)
                worker.start()

    def enqueue(self, prompt: str) -> DurableTask:
        if not prompt or not prompt.strip():
            raise ValueError("task prompt cannot be empty")
        task_id = "task_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._condition:
            self.connection.execute(
                "INSERT INTO runtime_tasks (id, status, prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, TaskStatus.ENQUEUED.value, prompt.strip(), now, now),
            )
            self.connection.commit()
            self._condition.notify_all()
            return self.find(task_id)  # type: ignore[return-value]

    def list(self, limit: int = 20) -> list[DurableTask]:
        bounded = max(1, min(100, int(limit)))
        with self._condition:
            rows = self.connection.execute(
                "SELECT * FROM runtime_tasks ORDER BY created_at DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            return [self._from_row(row) for row in rows]

    def find(self, task_id: str) -> DurableTask | None:
        if not task_id:
            return None
        with self._condition:
            row = self.connection.execute("SELECT * FROM runtime_tasks WHERE id = ?", (task_id.strip(),)).fetchone()
            return self._from_row(row) if row else None

    def cancel(self, task_id: str) -> bool:
        with self._condition:
            task = self.find(task_id)
            if task is None or task.terminal:
                return False
            self._mark_terminal(task.id, TaskStatus.CANCELED, task.result, "user canceled", task.started_at)
            self._condition.notify_all()
            return True

    def close(self) -> None:
        with self._condition:
            self._running = False
            self._condition.notify_all()
        for worker in list(self._workers):
            worker.join(timeout=2)
        self.connection.close()

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                if not self._running:
                    return
                task = self._claim_next_locked()
                if task is None:
                    self._condition.wait(timeout=0.3)
                    continue
                self._running_tasks[task.id] = threading.current_thread()
            started_at = _now()
            try:
                result = self.runner(task.prompt)
                with self._condition:
                    latest = self.find(task.id)
                    if latest is not None and latest.status != TaskStatus.CANCELED:
                        self._mark_terminal(task.id, TaskStatus.COMPLETED, result or "", "", started_at)
            except Exception as exc:
                with self._condition:
                    latest = self.find(task.id)
                    if latest is not None and latest.status != TaskStatus.CANCELED:
                        self._mark_terminal(task.id, TaskStatus.FAILED, "", str(exc), started_at)
            finally:
                with self._condition:
                    self._running_tasks.pop(task.id, None)
                    self._condition.notify_all()

    def _claim_next_locked(self) -> DurableTask | None:
        row = self.connection.execute(
            "SELECT * FROM runtime_tasks WHERE status = ? ORDER BY created_at ASC LIMIT 1",
            (TaskStatus.ENQUEUED.value,),
        ).fetchone()
        if row is None:
            return None
        now = _now()
        updated = self.connection.execute(
            "UPDATE runtime_tasks SET status = ?, started_at = ?, updated_at = ? WHERE id = ? AND status = ?",
            (TaskStatus.RUNNING.value, now, now, row["id"], TaskStatus.ENQUEUED.value),
        ).rowcount
        self.connection.commit()
        return self.find(row["id"]) if updated else None

    def _mark_terminal(self, task_id: str, status: TaskStatus, result: str, error: str, started_at: str) -> None:
        now = _now()
        duration = _duration_ms(started_at, now)
        self.connection.execute(
            """
            UPDATE runtime_tasks
            SET status = ?, result = ?, error = ?, finished_at = ?, duration_ms = ?, updated_at = ?
            WHERE id = ?
            """,
            (status.value, result or "", error or "", now, duration, now, task_id),
        )
        self.connection.commit()

    def _init_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                prompt TEXT NOT NULL,
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                duration_ms INTEGER DEFAULT 0
            )
            """
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_runtime_tasks_status ON runtime_tasks(status)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_runtime_tasks_created ON runtime_tasks(created_at)")
        self.connection.commit()

    def _recover_running_tasks(self) -> None:
        now = _now()
        self.connection.execute(
            "UPDATE runtime_tasks SET status = ?, updated_at = ? WHERE status = ?",
            (TaskStatus.ENQUEUED.value, now, TaskStatus.RUNNING.value),
        )
        self.connection.commit()

    def _from_row(self, row: sqlite3.Row) -> DurableTask:
        return DurableTask(
            row["id"],
            TaskStatus.from_value(row["status"]),
            row["prompt"],
            row["result"] or "",
            row["error"] or "",
            row["created_at"] or "",
            row["started_at"] or "",
            row["finished_at"] or "",
            int(row["duration_ms"] or 0),
        )


def default_task_db_path() -> Path:
    configured = os.getenv("PAICLI_TASK_DIR")
    base = Path(configured) if configured else Path.home() / ".paicli-py" / "tasks"
    return base / "tasks.db"


def format_task_list(tasks: list[DurableTask]) -> str:
    if not tasks:
        return "No background tasks."
    lines = [f"Recent background tasks: {len(tasks)}"]
    for task in tasks:
        lines.append(f"{task.id}  {task.status.value}  {task.duration_ms}ms  {task.short_prompt()}")
    return "\n".join(lines)


def format_task_log(task: DurableTask) -> str:
    lines = [f"Task {task.id}", f"Status: {task.status.value}", f"Created: {task.created_at}", "", "Prompt:", task.prompt]
    if task.started_at:
        lines.insert(3, f"Started: {task.started_at}")
    if task.finished_at:
        lines.insert(4, f"Finished: {task.finished_at} ({task.duration_ms}ms)")
    if task.error:
        lines.extend(["", "Error:", task.error])
    if task.result:
        lines.extend(["", "Result:", task.result])
    return "\n".join(lines).strip()


def handle_task_command(manager: DurableTaskManager, payload: str) -> str:
    normalized = (payload or "list").strip()
    if normalized.lower() == "list":
        return format_task_list(manager.list(20))
    if normalized.lower().startswith("list "):
        try:
            limit = int(normalized[5:].strip())
        except ValueError:
            limit = 20
        return format_task_list(manager.list(limit))
    if normalized.lower().startswith("add "):
        task = manager.enqueue(normalized[4:].strip())
        return f"background task submitted: {task.id}\n/task log {task.id}"
    if normalized.lower().startswith("cancel "):
        task_id = normalized[7:].strip()
        return f"cancel requested: {task_id}" if manager.cancel(task_id) else f"no cancelable task: {task_id}"
    if normalized.lower().startswith("log "):
        task_id = normalized[4:].strip()
        task = manager.find(task_id)
        return format_task_log(task) if task else f"task not found: {task_id}"
    return "Usage: /task [list [N] | add <prompt> | cancel <task_id> | log <task_id>]"


def _worker_count() -> int:
    try:
        return max(1, int(os.getenv("PAICLI_TASK_WORKERS", "2")))
    except ValueError:
        return 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started_at: str, finished_at: str) -> int:
    if not started_at:
        return 0
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
        return max(0, int((finish - start).total_seconds() * 1000))
    except ValueError:
        return 0
