import json
from pathlib import Path
from datetime import datetime

# Global paths
CONFIG_FILE = Path.home() / ".agentic-loop" / "config.json"
SESSIONS_DIR = Path.home() / ".agentic-loop" / "sessions"

# Ensure sessions directory exists
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

def _session_id() -> str:
    return datetime.now().strftime("ses_%Y%m%d_%H%M%S")

def _cleanup_old_sessions(max_sessions: int = 50):
    """Remove oldest sessions beyond max_sessions."""
    files = sorted(
        SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for f in files[max_sessions:]:
        try:
            f.unlink()
        except OSError:
            pass

def config_load() -> dict:
    """Load config from ~/.agentic-loop/config.json."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def config_save(config: dict):
    """Save config to ~/.agentic-loop/config.json with restricted permissions (owner-only)."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Ensure file exists and set owner-only permissions (600)
        CONFIG_FILE.touch(exist_ok=True)
        CONFIG_FILE.chmod(0o600)
        CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except Exception:
        pass
