#!/usr/bin/env python3
"""
Memory module — persistent local memory for the agent.
Extracted from loop.py for modularity.
Provides keyword-searchable JSONL-backed memory with human-readable HISTORY.md.
"""

import json
import re
from datetime import datetime
from pathlib import Path

MEMORY_FILE = ".agent_memory.jsonl"


def records() -> list:
    """Return all memory records from the JSONL file."""
    p = Path(MEMORY_FILE)
    if not p.exists():
        return []
    records_list = []
    for line in p.read_text(errors="replace").splitlines():
        try:
            records_list.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records_list


def load(query: str, max_results: int = 5) -> str:
    """Keyword-search persistent local memory."""
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9_]{3,}", query)}
    scored = []
    for rec in records():
        text = json.dumps(rec, ensure_ascii=False)
        hay = text.lower()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, rec))
    scored.sort(key=lambda x: (x[0], x[1].get("timestamp", "")), reverse=True)
    if not scored:
        return "[]"
    return json.dumps([rec for _, rec in scored[:max_results]], indent=2)


def save(note: str, kind: str = "note") -> str:
    """Append a local memory note. Also writes to memory/HISTORY.md."""
    ts = datetime.now().isoformat(timespec="seconds")
    rec = {
        "timestamp": ts,
        "kind": kind,
        "note": note[:2000],
    }
    with Path(MEMORY_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Append to human-readable history
    history_path = Path("memory/HISTORY.md")
    history_path.parent.mkdir(exist_ok=True)
    with history_path.open("a", encoding="utf-8") as f:
        f.write(f"### {ts} [{kind}]\n{note[:2000]}\n\n")

    return f"Saved memory note to {MEMORY_FILE} and updated memory/HISTORY.md"


def auto_context(user_message: str) -> str:
    """Load memory context relevant to the user message."""
    loaded = load(user_message, 4)
    if loaded == "[]":
        return ""
    return "Relevant local memory:\n" + loaded
