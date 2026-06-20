#!/usr/bin/env python3
"""
Mission module — persistent mission state for open-agent.
Tracks active goals, objectives, current focus, and progress.
Stored in ~/.agentic-loop/mission.json for cross-session persistence.

Usage:
    import mission
    mission.init("Prepare for interviews")
    mission.update_objective("DSA", status="in_progress", priority="high")
    mission.set_focus("System Design")
    state = mission.load()
    print(mission.render())
"""

import json
from datetime import datetime
from pathlib import Path

MISSION_DIR = Path.home() / ".agentic-loop"
MISSION_DIR.mkdir(parents=True, exist_ok=True)
MISSION_FILE = MISSION_DIR / "mission.json"


def load() -> dict:
    """Load the current mission state. Returns empty mission if none exists."""
    if not MISSION_FILE.exists():
        return {
            "statement": "",
            "objectives": [],
            "current_focus": "",
            "created_at": "",
            "updated_at": "",
        }
    try:
        data = json.loads(MISSION_FILE.read_text())
        if "objectives" not in data:
            data["objectives"] = []
        return data
    except (json.JSONDecodeError, KeyError):
        return {
            "statement": "",
            "objectives": [],
            "current_focus": "",
            "created_at": "",
            "updated_at": "",
        }


def save(mission: dict) -> str:
    """Persist mission state to disk and update shadow context file."""
    mission["updated_at"] = datetime.now().isoformat(timespec="seconds")
    MISSION_FILE.write_text(json.dumps(mission, indent=2, default=str))
    # Also write .mission_state.txt for backward-compatible shadow context
    _write_shadow(mission)
    return "MISSION SAVED"


def init(statement: str = "") -> dict:
    """Initialise a fresh mission structure."""
    now = datetime.now().isoformat(timespec="seconds")
    mission = {
        "statement": statement,
        "objectives": [],
        "current_focus": "",
        "created_at": now,
        "updated_at": now,
    }
    save(mission)
    return mission


def update_objective(
    title: str, status: str = "pending", priority: str = "medium"
) -> dict:
    """Add or update an objective in the mission."""
    mission = load()
    now = datetime.now().isoformat(timespec="seconds")
    for obj in mission["objectives"]:
        if obj["title"] == title:
            obj["status"] = status
            obj["priority"] = priority
            obj["updated_at"] = now
            save(mission)
            return mission
    mission["objectives"].append(
        {
            "title": title,
            "status": status,
            "priority": priority,
            "created_at": now,
            "updated_at": now,
        }
    )
    save(mission)
    return mission


def set_focus(focus: str) -> dict:
    """Set the current focus area."""
    mission = load()
    mission["current_focus"] = focus
    save(mission)
    return mission


def render() -> str:
    """Render the mission state as plain text (no ANSI codes)."""
    mission = load()
    if not mission.get("objectives") and not mission.get("statement"):
        return ""
    lines = []
    if mission.get("statement"):
        lines.append(f"MISSION: {mission['statement'][:200]}")
    if mission.get("current_focus"):
        lines.append(f"CURRENT FOCUS: {mission['current_focus'][:100]}")
    if mission["objectives"]:
        lines.append("MISSION STATUS:")
        for obj in mission["objectives"]:
            status_icon = {
                "in_progress": "▶",
                "done": "✓",
                "failed": "✗",
                "pending": "○",
            }.get(obj.get("status", "pending"), "○")
            title = obj.get("title", "Untitled")
            lines.append(f"  {status_icon} {title}")
    return "\n".join(lines)


def clear() -> str:
    """Clear the mission state."""
    init()
    return "Mission cleared."


def has_active_mission() -> bool:
    """Check if there's an active mission with objectives or a statement."""
    mission = load()
    return bool(mission.get("objectives")) or bool(mission.get("statement"))


def migrate_from_todo_file():
    """Import objectives from legacy .agent_todo.json if it exists."""
    todo_path = Path(".agent_todo.json")
    if not todo_path.exists():
        return
    try:
        todos = json.loads(todo_path.read_text())
        mission = load()
        for t in todos:
            title = t.get("title", "Untitled")
            # Skip duplicates
            if any(o["title"] == title for o in mission["objectives"]):
                continue
            mission["objectives"].append(
                {
                    "title": title,
                    "status": t.get("status", "pending"),
                    "priority": t.get("priority", "medium"),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
        save(mission)
        # Archive old file
        todo_path.rename(todo_path.with_suffix(".json.bak"))
    except (json.JSONDecodeError, Exception):
        pass


# ── Private helpers ──


def _write_shadow(mission: dict):
    """Write plain-text mission summary to .mission_state.txt for agent context."""
    text = _to_shadow_text(mission)
    Path(".mission_state.txt").write_text(text)


def _to_shadow_text(mission: dict) -> str:
    """Convert mission to compact shadow-context text."""
    parts = []
    if mission.get("statement"):
        parts.append(f"Mission: {mission['statement'][:200]}")
    if mission.get("current_focus"):
        parts.append(f"Current Focus: {mission['current_focus'][:100]}")
    if mission["objectives"]:
        parts.append("Objectives:")
        for obj in mission["objectives"]:
            parts.append(
                f"  [{obj.get('status', 'pending')}] {obj['title'][:100]} ({obj.get('priority', 'medium')})"
            )
    return "\n".join(parts)
