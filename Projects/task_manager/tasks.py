"""Task Manager – add, list, complete, delete tasks."""

import uuid
from datetime import datetime, date
from typing import List, Optional

from .storage import _load, _save


class Task:
    id: str
    title: str
    description: str
    due_date: Optional[date]
    status: str  # "pending" | "completed"
    created_at: str

    def __init__(self, title: str, description: str = "", due_date: Optional[date] = None):
        self.id = str(uuid.uuid4())[:8]
        self.title = title
        self.description = description
        self.due_date = due_date
        self.status = "pending"
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_date": str(self.due_date) if self.due_date else None,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        obj = cls(title=d["title"], description=d.get("description", ""), due_date=None)
        obj.id = d["id"]
        obj.status = d["status"]
        obj.created_at = d["created_at"]
        if d.get("due_date"):
            obj.due_date = date.fromisoformat(d["due_date"])
        return obj


class TaskManager:
    def __init__(self):
        self.tasks: List[Task] = [Task.from_dict(t) for t in _load()]

    def save(self):
        _save([t.to_dict() for t in self.tasks])

    # ── CRUD ────────────────────────────────────────────────
    def add_task(self, title: str, description: str = "", due_date: Optional[date] = None) -> Task:
        task = Task(title, description, due_date)
        self.tasks.append(task)
        self.save()
        return task

    def list_tasks(self, status: Optional[str] = None) -> List[Task]:
        if status:
            return [t for t in self.tasks if t.status == status]
        return list(self.tasks)

    def complete_task(self, task_id: str) -> Task:
        for t in self.tasks:
            if t.id == task_id and t.status == "pending":
                t.status = "completed"
                self.save()
                return t
        raise ValueError(f"Task {task_id} not found or already completed")

    def delete_task(self, task_id: str) -> None:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        if len(self.tasks) == before:
            raise ValueError(f"Task {task_id} not found")
        self.save()

    def get_task(self, task_id: str) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise ValueError(f"Task {task_id} not found")
