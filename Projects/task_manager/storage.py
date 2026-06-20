"""JSON file storage for tasks."""

import json, os

DB_PATH = os.path.join(os.path.dirname(__file__), "tasks.json")


def _load() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    with open(DB_PATH) as f:
        return json.load(f)


def _save(tasks: list[dict]) -> None:
    with open(DB_PATH, "w") as f:
        json.dump(tasks, f, indent=2)
