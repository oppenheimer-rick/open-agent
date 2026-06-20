#!/usr/bin/env python3
"""
Out-of-the-Box: Dynamic context layer for open-agent.
─────────────────────────────────────────────────────────
A persistent, self-improving module that provides the agent with
structured, curated context — mission state, critical user info,
LLM-generated insights — instead of fetching raw memory or scrolling
through past chats.

The LLM uses this as its primary context layer. Every conversation
enriches it. Over time it builds a rich, structured understanding of
the user's goals, preferences, and completed work.

Storage: ~/.agentic-loop/out-of-the-box.json
"""

import json
import re
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / ".agentic-loop"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONTEXT_FILE = DATA_DIR / "out-of-the-box.json"


def _default() -> dict:
    return {
        "version": 2,
        "mission_statement": "",
        "active_objectives": [],
        "completed_objectives": [],
        "critical_info": [],
        "insights": [],
        "last_updated": "",
        "improvement_count": 0,
    }


# ── Load / Save ────────────────────────────────────────────────────────────────


def load() -> dict:
    """Load the full context dictionary from disk."""
    if not CONTEXT_FILE.exists():
        ctx = _default()
        save(ctx)
        return ctx
    try:
        data = json.loads(CONTEXT_FILE.read_text())
        default = _default()
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, Exception):
        return _default()


def save(ctx: dict) -> str:
    """Persist context to disk and return status message."""
    ctx["last_updated"] = datetime.now().isoformat(timespec="seconds")
    CONTEXT_FILE.write_text(json.dumps(ctx, indent=2, default=str))
    return "OOTB_SAVED"


# ── Mission Management ─────────────────────────────────────────────────────────


def update_mission(statement: str) -> dict:
    """Set or update the overarching mission statement."""
    ctx = load()
    ctx["mission_statement"] = statement
    save(ctx)
    return ctx


def add_objective(
    title: str, status: str = "pending", priority: str = "medium"
) -> dict:
    """Add or update an active objective. Uses title for dedup."""
    ctx = load()
    now = datetime.now().isoformat(timespec="seconds")
    for obj in ctx["active_objectives"]:
        if obj["title"] == title:
            obj["status"] = status
            obj["priority"] = priority
            obj["updated_at"] = now
            save(ctx)
            return ctx
    ctx["active_objectives"].append(
        {
            "title": title,
            "status": status,
            "priority": priority,
            "created_at": now,
            "updated_at": now,
        }
    )
    save(ctx)
    return ctx


def complete_objective(title: str) -> dict:
    """Mark an objective as completed (moves from active to completed)."""
    ctx = load()
    ctx["active_objectives"] = [
        o for o in ctx["active_objectives"] if o["title"] != title
    ]
    ctx["completed_objectives"].append(
        {
            "title": title,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    save(ctx)
    return ctx


# ── Critical User Info ─────────────────────────────────────────────────────────


def add_critical_info(
    fact: str, source: str = "conversation", confidence: str = "medium"
) -> dict:
    """Store an important fact about the user. Avoids exact duplicates."""
    ctx = load()
    for info in ctx["critical_info"]:
        if info["fact"] == fact:
            return ctx
    ctx["critical_info"].append(
        {
            "fact": fact,
            "source": source,
            "confidence": confidence,
            "added_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    save(ctx)
    return ctx


def remove_critical_info(fact: str) -> dict:
    """Remove a fact by exact match."""
    ctx = load()
    ctx["critical_info"] = [i for i in ctx["critical_info"] if i["fact"] != fact]
    save(ctx)
    return ctx


# ── Insights (LLM-generated observations) ──────────────────────────────────────


def add_insight(observation: str, reasoning: str = "") -> dict:
    """Record an LLM-generated insight from conversation analysis."""
    ctx = load()
    ctx["insights"].append(
        {
            "observation": observation,
            "reasoning": reasoning,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
    )
    ctx["improvement_count"] = ctx.get("improvement_count", 0) + 1
    # Keep only last 20 insights to prevent bloat
    ctx["insights"] = ctx["insights"][-20:]
    save(ctx)
    return ctx


def recent_insights(count: int = 5) -> list:
    """Return the N most recent insights."""
    ctx = load()
    return ctx.get("insights", [])[-count:]


# ── Render for System Prompt ────────────────────────────────────────────────────


def render() -> str:
    """
    Full context block for injection into the system prompt.
    The agent sees this at the start of every conversation.
    """
    ctx = load()
    parts = []

    if ctx.get("mission_statement"):
        parts.append(f"Mission Statement: {ctx['mission_statement']}")

    if ctx.get("active_objectives"):
        parts.append("Active Objectives:")
        for obj in ctx["active_objectives"]:
            icon = {"in_progress": "▶", "pending": "○"}.get(
                obj.get("status", "pending"), "○"
            )
            parts.append(f"  {icon} {obj['title']} ({obj.get('priority', 'medium')})")

    if ctx.get("completed_objectives"):
        parts.append("Completed Objectives:")
        for obj in ctx["completed_objectives"]:
            parts.append(f"  ✓ {obj['title']}")

    if ctx.get("critical_info"):
        parts.append("Critical User Information:")
        for info in ctx["critical_info"]:
            parts.append(f"  • {info['fact']}")

    if ctx.get("insights"):
        parts.append("Recent Insights (LLM-generated):")
        for ins in ctx["insights"][-5:]:
            parts.append(f"  ▸ {ins['observation']}")

    parts.append(
        f"\n[Context last updated: {ctx.get('last_updated', 'never')} | "
        f"Improved {ctx.get('improvement_count', 0)} times]"
    )

    return "\n".join(parts)


def render_short() -> str:
    """One-line summary for terminal toolbar or quick status."""
    ctx = load()
    active = len(ctx.get("active_objectives", []))
    completed = len(ctx.get("completed_objectives", []))
    mission = ctx.get("mission_statement", "")
    tag = f" {mission[:50]}" if mission else ""
    insights = ctx.get("improvement_count", 0)
    return f"🧠{tag} | {active} active · {completed} done · {insights} insights"


def status() -> str:
    """Human-readable status dump."""
    ctx = load()
    lines = ["── Out-of-the-Box Context ──"]
    lines.append(f"  Last updated: {ctx.get('last_updated', 'never')}")
    lines.append(f"  Improvement count: {ctx.get('improvement_count', 0)}")
    lines.append(f"  Mission: {ctx.get('mission_statement', '(none)')[:80]}")
    lines.append(f"  Active objectives: {len(ctx.get('active_objectives', []))}")
    lines.append(f"  Completed objectives: {len(ctx.get('completed_objectives', []))}")
    lines.append(f"  Critical info facts: {len(ctx.get('critical_info', []))}")
    lines.append(f"  Insights stored: {len(ctx.get('insights', []))}")
    return "\n".join(lines)


# ── LLM Tool Interface ─────────────────────────────────────────────────────────
# These are called by the agent's TOOL_MAP in loop.py.
# The LLM invokes them to update the context autonomously.


def tool_analyze_and_improve(messages_snapshot: str) -> str:
    """
    TOOL: Analyse recent conversation and improve the context.
    Called by the agent after each response cycle.
    The LLM passes a snapshot of recent messages and this function
    extracts key information to update mission, objectives, and critical info.

    Returns a summary of what was learned and updated.
    """
    ctx = load()
    changes = []

    # Parse the snapshot for mission-related keywords
    snapshot_lower = messages_snapshot.lower()

    # Look for mission statements
    mission_patterns = [
        r"(?:my goal is|i want to|my mission is|i'm working on|i need to)\s+(.+?)[.!\n]",
        r"(?:help me|assist me|guide me)\s+(?:with|on|in)\s+(.+?)[.!\n]",
    ]
    for pat in mission_patterns:
        match = re.search(pat, snapshot_lower)
        if match:
            statement = match.group(1).strip().capitalize()
            if len(statement) > 10 and statement != ctx.get("mission_statement", ""):
                ctx["mission_statement"] = statement
                changes.append(f"Updated mission: {statement[:80]}")
                break

    # Look for new objectives
    obj_patterns = [
        r"(?:i need to|i have to|i should|let's|we need to)\s+(.+?)[.!\n]",
        r"(?:focus on|work on|start)\s+(.+?)[.!\n]",
    ]
    for pat in obj_patterns:
        for match in re.finditer(pat, snapshot_lower):
            title = match.group(1).strip().capitalize()
            if len(title) > 5 and not any(
                o["title"] == title for o in ctx["active_objectives"]
            ):
                ctx["active_objectives"].append(
                    {
                        "title": title,
                        "status": "pending",
                        "priority": "medium",
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                changes.append(f"Added objective: {title[:60]}")
                if len(changes) >= 3:
                    break
        if len(changes) >= 3:
            break

    save(ctx)

    if not changes:
        return "No new information extracted from recent conversation."

    return "Out-of-the-Box context updated:\n" + "\n".join(f"  • {c}" for c in changes)


def tool_update_objective(title: str, status: str = "in_progress") -> str:
    """TOOL: Update the status of an active objective."""
    ctx = load()
    for obj in ctx["active_objectives"]:
        if obj["title"] == title:
            obj["status"] = status
            obj["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save(ctx)
            return f"Objective '{title}' → {status}"
    return f"Objective '{title}' not found in active objectives."


def tool_add_info(fact: str) -> str:
    """TOOL: Add a critical user fact to context."""
    add_critical_info(fact, source="llm-inference", confidence="medium")
    return f"Noted: {fact[:100]}"


def tool_mission(statement: str) -> str:
    """TOOL: Set or update the mission statement."""
    update_mission(statement)
    return f"Mission updated: {statement[:120]}"


# ── Cleanup ─────────────────────────────────────────────────────────────────────


def clear() -> str:
    """Reset all context."""
    save(_default())
    return "OOTB_CLEARED"


# ── Import from legacy systems ─────────────────────────────────────────────────


def import_from_mission():
    """Import state from mission.py (mission.json) if it exists."""
    mission_path = DATA_DIR / "mission.json"
    if not mission_path.exists():
        return "No mission.json to import."
    try:
        data = json.loads(mission_path.read_text())
        ctx = load()
        if data.get("statement") and not ctx.get("mission_statement"):
            ctx["mission_statement"] = data["statement"]
        for obj in data.get("objectives", []):
            status = obj.get("status", "pending")
            if status == "done":
                ctx["completed_objectives"].append(
                    {
                        "title": obj["title"],
                        "completed_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            else:
                ctx["active_objectives"].append(
                    {
                        "title": obj["title"],
                        "status": status,
                        "priority": obj.get("priority", "medium"),
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
        save(ctx)
        mission_path.rename(mission_path.with_suffix(".json.imported"))
        return (
            f"Imported {len(data.get('objectives', []))} objectives from mission.json"
        )
    except Exception as e:
        return f"Import failed: {e}"


if __name__ == "__main__":
    # CLI test
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(status())
    elif cmd == "render":
        print(render())
    elif cmd == "clear":
        print(clear())
    elif cmd == "import":
        print(import_from_mission())
    else:
        print(f"Unknown command: {cmd}")
