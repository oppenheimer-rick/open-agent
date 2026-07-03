import json
from datetime import datetime
from pathlib import Path
from core.config import SESSIONS_DIR, _session_id, _cleanup_old_sessions

def session_save(messages: list, mode: str, task: str, session_id: str = None) -> str:
    """Persist a session to disk."""
    if not session_id:
        session_id = _session_id()
    session = {
        "id": session_id,
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "task": task[:200],
        "message_count": len(messages),
        "messages": messages,
    }
    path = SESSIONS_DIR / f"{session_id}.json"
    path.write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")
    _cleanup_old_sessions()
    return session_id

def session_load(session_id: str) -> dict | None:
    """Load a full session from disk."""
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def session_list(limit: int = 20) -> list:
    """List recent sessions (metadata only, no messages)."""
    files = sorted(
        SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    sessions = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append(
                {
                    "id": data.get("id", f.stem),
                    "timestamp": data.get("timestamp", ""),
                    "mode": data.get("mode", "general"),
                    "task": data.get("task", "")[:120],
                    "message_count": data.get("message_count", 0),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return sessions

def session_search(query: str, limit: int = 20) -> list:
    """Full-text search across all session messages."""
    query = query.lower()
    results = []
    for f in sorted(
        SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            full_text = json.dumps(data).lower()
            if query in full_text:
                results.append(
                    {
                        "id": data.get("id", f.stem),
                        "timestamp": data.get("timestamp", ""),
                        "mode": data.get("mode", "general"),
                        "task": data.get("task", "")[:120],
                        "message_count": data.get("message_count", 0),
                    }
                )
            if len(results) >= limit:
                break
        except (json.JSONDecodeError, KeyError):
            continue
    return results

def session_rename(session_id: str, new_name: str) -> bool:
    """Rename a session's task/title."""
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["task"] = new_name[:200]
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return True
    except (json.JSONDecodeError, KeyError):
        return False
