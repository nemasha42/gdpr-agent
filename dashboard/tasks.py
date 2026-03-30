"""Simple in-process background task runner for Flask pipeline routes."""

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

# task_id → {name, status, progress, total, result, error, started_at, finished_at}
_tasks: dict[str, dict] = {}
_lock = threading.Lock()


def start_task(name: str, fn: Callable, *args: Any, **kwargs: Any) -> str:
    """Spawn *fn* in a daemon thread and return its task_id.

    The task dict is immediately visible via :func:`get_task`.
    """
    task_id = f"{name}-{uuid.uuid4().hex[:8]}"
    with _lock:
        _tasks[task_id] = {
            "id": task_id,
            "name": name,
            "status": "running",
            "progress": 0,
            "total": 0,
            "result": None,
            "error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

    def _run() -> None:
        try:
            result = fn(task_id, *args, **kwargs)
            with _lock:
                _tasks[task_id]["status"] = "done"
                _tasks[task_id]["result"] = result
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["error"] = str(exc)
        finally:
            with _lock:
                _tasks[task_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return task_id


def get_task(task_id: str) -> dict | None:
    """Return a copy of the task dict, or None if unknown."""
    with _lock:
        task = _tasks.get(task_id)
        return dict(task) if task else None


def update_task_progress(task_id: str, progress: int, total: int = 0) -> None:
    """Update progress counters for a running task."""
    with _lock:
        if task_id in _tasks:
            _tasks[task_id]["progress"] = progress
            if total:
                _tasks[task_id]["total"] = total


def find_running_task(name: str) -> dict | None:
    """Return the first running task whose name starts with *name*, or None."""
    with _lock:
        for task in _tasks.values():
            if task["name"].startswith(name) and task["status"] == "running":
                return dict(task)
    return None


def all_tasks() -> list[dict]:
    """Return copies of all known tasks (most recent first)."""
    with _lock:
        tasks = [dict(t) for t in _tasks.values()]
    tasks.sort(key=lambda t: t.get("started_at", ""), reverse=True)
    return tasks
