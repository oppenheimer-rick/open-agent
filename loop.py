#!/usr/bin/env python3
"""
open-agent — local-first terminal IDE agent.

Usage:
  open-agent                          → interactive REPL
  open-agent "your task here"         → single shot
  open-agent "refactor x.py" --coding → structured coding agent
  open-agent "your task" --steps 200  → more steps allowed
"""

# ── Imports ────────────────────────────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import os
import json
import time
import subprocess
import re
import ast
import threading
import argparse
import tempfile
import shutil
import httpx
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Modules ──
import memory
import mission
import out_of_the_box as ootb
import job_search
import providers
import youtube_utils

# ── File Watcher (Bidirectional TUI Sync) ──
import queue

FILE_CHANGES_QUEUE = queue.Queue()
AGENT_MODIFIED_FILES = {}  # path -> timestamp
ACTIVE_FILES = set()  # Recently read/written files


class FileChangeHandler(FileSystemEventHandler):
    def _is_agent_change(self, path: str) -> bool:
        if path in AGENT_MODIFIED_FILES:
            if time.time() - AGENT_MODIFIED_FILES[path] < 2.0:
                return True
        return False

    def on_modified(self, event):
        if not event.is_directory:
            p = Path(event.src_path)
            if (
                p.name.startswith(".")
                or "agentic-loop" not in p.parts
                or "venv" in p.parts
            ):
                return
            if self._is_agent_change(str(p.absolute())):
                return
            FILE_CHANGES_QUEUE.put({"type": "modified", "path": str(p)})

    def on_created(self, event):
        if not event.is_directory:
            p = Path(event.src_path)
            if p.name.startswith(".") or "venv" in p.parts:
                return
            if self._is_agent_change(str(p.absolute())):
                return
            FILE_CHANGES_QUEUE.put({"type": "created", "path": str(p)})

    def on_deleted(self, event):
        if not event.is_directory:
            p = Path(event.src_path)
            if p.name.startswith(".") or "venv" in p.parts:
                return
            if self._is_agent_change(str(p.absolute())):
                return
            FILE_CHANGES_QUEUE.put({"type": "deleted", "path": str(p)})


# ── Prompt Toolkit ──
from prompt_toolkit import PromptSession, HTML
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML as FormattedHTML
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer

try:
    from pygments.lexers.markup import MarkdownLexer
except ImportError:
    MarkdownLexer = None

# ── Config ─────────────────────────────────────────────────────────────────────
LLM_BASE = providers.BASE_URL
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8081/search")
MODEL = providers.get_model()
MAX_TOKENS = providers.get_max_tokens()
TEMPERATURE = providers.get_temperature()
MAX_STEPS = 500  # hard cap on agentic loop iterations
MAX_RETRIES = 3  # retries per TODO item in coding mode
PYTHON_TIMEOUT = 30  # seconds for sandboxed python
TODO_FILE = ".agent_todo.json"
MEMORY_FILE = ".agent_memory.jsonl"

LOGO_WIDE = r"""
┌───────────────────────────────────────────────────────────────┐
│                                                               │
│   ░░▒▒▓▓  █▀█ █▀█ █▀▀ █▄ █    ▄▀█ █▀▀ █▀▀ █▄ █ ▀█▀   ▓▓▒▒░░   │
│   ░░▒▒▓▓  █▄█ █▀▀ ██▄ █ ▀█    █▀█ █▄█ ██▄ █ ▀█  █    ▓▓▒▒░░   │
│                                                               │
│       local-first · privacy-first · intelligence-driven       │
│                                                               │
└───────────────────────────────────────────────────────────────┘
"""

LOGO_NARROW = r"""
┌────────────────────────────────────────────────────┐
│  ░▒▓ █▀█ █▀█ █▀▀ █▄ █ ▄▄ ▄▀█ █▀▀ █▀▀ █▄ █ ▀█▀ ▓▒░  │
│  ░▒▓ █▄█ █▀▀ ██▄ █ ▀█    █▀█ █▄█ ██▄ █ ▀█  █  ▓▒░  │
└────────────────────────────────────────────────────┘
"""


class AgentInterrupted(Exception):
    """Raised when the user interrupts an active agent run."""


# ── ANSI / Terminal ────────────────────────────────────────────────────────────
class C:
    """ANSI color codes — Claude-inspired Palette"""

    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITAL = "\033[3m"
    UL = "\033[4m"
    # Claude-inspired Tones
    RED = "\033[38;2;255;95;87m"
    GREEN = "\033[38;2;0;200;150m"
    YELLOW = "\033[38;2;255;190;0m"
    BLUE = "\033[38;2;121;182;242m"
    PURPLE = "\033[38;2;171;146;229m"
    CYAN = "\033[38;2;48;169;222m"
    ORANGE = "\033[38;2;242;176;158m"
    PINK = "\033[38;2;255;120;180m"
    GRAY = "\033[38;2;160;160;160m"
    WHITE = "\033[38;2;240;240;240m"
    BLACK = "\033[38;2;20;20;20m"

    # Backgrounds
    BG_CYAN = "\033[48;2;48;169;222m"
    BG_GRAY = "\033[48;2;60;60;60m"
    BG_PURPLE = "\033[48;2;171;146;229m"
    BG_BLACK = "\033[48;2;20;20;20m"
    BG_RED = "\033[48;2;255;95;87m"


def co(color, text):
    return f"{color}{text}{C.RST}"


def bold(t):
    return co(C.BOLD, t)


def dim(t):
    return co(C.DIM + C.GRAY, t)


def terminal_width(default: int = 80) -> int:
    return max(40, shutil.get_terminal_size((default, 24)).columns)


def trunc(text: str, width: int) -> str:
    text = str(text)
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


# ── Spinner ─────────────────────────────────────────────────────────────────
class Spinner:
    """Braille spinner — High frequency punchy"""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label="Thinking"):
        self.label = label
        self._stop = threading.Event()
        self._alive = False

    def _run(self):
        i = 0
        while not self._stop.is_set():
            f = self.FRAMES[i % len(self.FRAMES)]
            # Pulsing effect or just neon
            sys.stdout.write(f"\r{co(C.CYAN, f)} {co(C.BOLD + C.WHITE, self.label)}   ")
            sys.stdout.flush()
            time.sleep(0.05)
            i += 1

    def start(self):
        if self._alive:
            return
        self._alive = True
        self._stop.clear()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._alive = False
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()


# ── UI Primitives (Punchy Agent style) ─────────────────────────────────────────
TOOL_ICONS = {
    "outline_file": "📂",
    "read_file_section": "📖",
    "write_file": "💾",
    "patch_file": "🛠️ ",
    "grep_codebase": "🔍",
    "graph_search": "🔭",
    "search_web": "🌍",
    "run_bash": "⚡",
    "run_python": "🐍",
    "memory_load": "🧠",
    "memory_save": "🧬",
    "todo_read": "📝",
    "todo_write": "📝",
    "todo_update": "✨",
    "git_status": "🎋",
}


def ui_tool_call(name, args: dict, result=None, error=False):
    """Render a tool call block — Minimal & Clean"""
    icon = TOOL_ICONS.get(name, "⚙️ ")
    width = min(92, max(52, terminal_width() - 4))

    # COMPACT MODE for common file tools
    compact_map = {
        "read_file_section": ("ReadFile", C.GREEN),
        "patch_file": ("Edit", C.BLUE),
        "outline_file": ("Outline", C.PURPLE),
        "grep_codebase": ("Grep", C.YELLOW),
        "write_file": ("Write", C.PINK),
        "todo_update": ("Todo", C.YELLOW),
        "todo_write": ("TodoPlan", C.CYAN),
    }

    if name in compact_map and result is not None:
        label, base_color = compact_map[name]
        check = co(C.GREEN, "✓") if not error else co(C.RED, "✖")

        summary = ""
        if name == "read_file_section":
            summary = f"{args.get('path')} → Read lines {args.get('start_line')}-{args.get('end_line')}"
        elif name == "patch_file":
            summary = f"{args.get('path')} → Applied precision patch"
            if "Patched:" in str(result):
                m = re.search(r"\((.+)\)", str(result))
                if m:
                    summary += f" ({m.group(1)})"
            print(
                f"  {check}  {co(C.BOLD + base_color, label.ljust(9))} {co(C.WHITE, summary)}"
            )
            # Show red/green diff lines
            result_str = str(result)
            if "\n" in result_str:
                diff_part = result_str.split("\n", 1)[1]
                for line in diff_part.splitlines()[:6]:
                    print(f"  {line}")
                if len(diff_part.splitlines()) > 6:
                    print(dim("  ... more lines"))
            return
        elif name == "outline_file":
            summary = f"{args.get('path')} → Structural map generated"
        elif name == "write_file":
            summary = f"{args.get('path')} → New file created"
        elif name == "todo_update":
            summary = f"Item {args.get('item_id')} → {args.get('status')}"
        elif name == "todo_write":
            summary = (
                f"Generated {str(result).split(' ')[1]} task(s)"
                if "Saved" in str(result)
                else "Updated tasks"
            )

        print(
            f"  {check}  {co(C.BOLD + base_color, label.ljust(9))} {co(C.WHITE, summary)}"
        )
        return

    color = C.RED if error else C.CYAN
    label = f" {icon} {name.upper()} "

    # Minimal single-line header
    print(co(color, "  ⚡ " + label.strip() + " " + "─" * (width - len(label) - 6)))

    for k, v in args.items():
        val = str(v)
        if len(val) > width - 10:
            val = trunc(val, width - 13)
        print(f"  {co(C.PINK, '•')} {co(C.GRAY, k + ':')} {co(C.WHITE, val)}")

    if result is not None:
        result_lines = str(result).splitlines()
        r_color = C.RED if error else C.GRAY
        preview = result_lines[:15]
        for ln in preview:
            print(f"    {co(r_color, trunc(ln, width - 6))}")
        if len(result_lines) > 15:
            print(dim(f"    ... {len(result_lines) - 15} more lines"))

    print(co(color, "  " + "─" * (width - 2)))


def ui_step_banner(step: int, finish_reason: str = ""):
    """Minimal loop progress (one-liner if needed, or silent)"""
    pass


def ui_header(title: str, subtitle: str = ""):
    width = min(92, terminal_width())
    print(
        f"\n{co(C.BOLD + C.WHITE, '── ' + title.upper() + ' ' + '─' * (width - len(title) - 5))}"
    )
    if subtitle:
        print(f"  {co(C.CYAN, '↪')} {co(C.WHITE, trunc(subtitle, width - 6))}")


def ui_error(text: str):
    print(f"\n{co(C.RED, '✖')} {co(C.RED + C.BOLD, 'ERROR:')} {co(C.WHITE, text)}")


def ui_info(text: str):
    print(f"  {co(C.BLUE, 'ℹ')} {dim(text)}")


def ui_token_usage(usage: dict, step: int):
    i = usage.get("prompt_tokens", 0)
    o = usage.get("completion_tokens", 0)
    print(dim(f"  ⚡ {i} in / {o} out  (step {step})"))


def ui_banner():
    width = terminal_width()
    logo = LOGO_WIDE if width >= 76 else LOGO_NARROW

    # Header centering (Removed "Open-agent" text)
    print("\n")

    # Centered Logo
    for line in logo.strip("\n").splitlines():
        print(line.center(width))
    print()


# ── Session History ────────────────────────────────────────────────────────────
SESSIONS_DIR = Path.home() / ".agentic-loop" / "sessions"
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
    path.write_text(json.dumps(session, indent=2, default=str))
    _cleanup_old_sessions()
    return session_id


CONFIG_FILE = Path.home() / ".agentic-loop" / "config.json"


def config_load() -> dict:
    """Load config from ~/.agentic-loop/config.json."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def config_save(config: dict):
    """Save config to ~/.agentic-loop/config.json."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
    except Exception:
        pass


def llm_generate(system_prompt: str, user_prompt: str, max_tokens: int = 512) -> str:
    """Direct, synchronous call to the LLM backend via provider abstraction."""
    return providers.generate(system_prompt, user_prompt, max_tokens)


OBSIDIAN_INSIGHTS = ""


def fetch_hn_top_stories() -> list:
    """Fetch the top 3 newest stories from Hacker News."""
    stories = []
    try:
        with httpx.Client(timeout=0.6) as client:
            resp = client.get("https://hacker-news.firebaseio.com/v0/newstories.json")
            if resp.status_code == 200:
                new_ids = resp.json()[:3]
                for item_id in new_ids:
                    try:
                        item_resp = client.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
                        if item_resp.status_code == 200:
                            data = item_resp.json()
                            stories.append({
                                "title": data.get("title", "No Title"),
                                "url": data.get("url", f"https://news.ycombinator.com/item?id={item_id}")
                            })
                    except Exception:
                        pass
    except Exception:
        pass
    return stories


def fetch_random_wikipedia_article() -> dict | None:
    """Fetch an interesting Wikipedia article from the curated "Unusual Articles" page."""
    try:
        import random
        from bs4 import BeautifulSoup
        
        headers = {"User-Agent": "open-agent/1.0 (contact: github.com/oppenheimer-rick/open-agent)"}
        with httpx.Client(timeout=1.5, headers=headers) as client:
            r = client.get("https://en.wikipedia.org/wiki/Wikipedia:Unusual_articles")
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                content = soup.find(id="mw-content-text")
                links = []
                if content:
                    for a in content.find_all("a", href=True):
                        href = a["href"]
                        if href.startswith("/wiki/") and not any(ns in href for ns in [":", "Main_Page", "Unusual_articles"]):
                            title = href.split("/wiki/")[1]
                            links.append(title)
                
                if links:
                    chosen_title = random.choice(links)
                    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{chosen_title}"
                    r_sum = client.get(summary_url)
                    if r_sum.status_code == 200:
                        data = r_sum.json()
                        title = data.get("title", chosen_title.replace("_", " "))
                        url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
                        extract = data.get("extract", "")
                        
                        if extract:
                            wrapped_lines = []
                            words = extract.split()
                            curr_line = ""
                            for w in words:
                                if len(curr_line) + len(w) + 1 > 70:
                                    wrapped_lines.append(curr_line)
                                    curr_line = w
                                else:
                                    curr_line += (" " if curr_line else "") + w
                            if curr_line:
                                wrapped_lines.append(curr_line)
                            
                            return {
                                "title": title,
                                "extract_lines": wrapped_lines[:8],
                                "url": url
                            }
    except Exception:
        pass
    return None


def jarvis_system_check() -> str:
    import platform
    import getpass
    from datetime import datetime
    
    # 1. Greetings
    hour = datetime.now().hour
    if 5 <= hour < 12:
        greeting = "Good morning, Sir. Diagnostics are green."
    elif 12 <= hour < 17:
        greeting = "Good afternoon, Sir. Core temperature is nominal."
    elif 17 <= hour < 22:
        greeting = "Good evening, Sir. All systems operating within standard parameters."
    else:
        greeting = "Working late, Sir? The Mark XLIII armor is on standby."

    # 2. Battery / Arc Reactor Status
    battery_status = "Arc Reactor Core: 100% (Stable)"
    try:
        bat_dir = Path("/sys/class/power_supply")
        if bat_dir.exists():
            for b in bat_dir.glob("BAT*"):
                cap_file = b / "capacity"
                status_file = b / "status"
                if cap_file.exists():
                    cap = cap_file.read_text().strip()
                    status = status_file.read_text().strip() if status_file.exists() else "Discharging"
                    battery_status = f"Arc Reactor Core: {cap}% ({status})"
                    break
    except Exception:
        pass

    # 3. Workspace Integrity Check (Git Status)
    git_status = "Clean (nominal)"
    try:
        res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=0.5)
        if res.returncode == 0:
            modified = [line for line in res.stdout.splitlines() if line.strip()]
            if modified:
                git_status = f"{len(modified)} files modified (uncommitted)"
    except Exception:
        pass

    # 4. System Load average (Linux only)
    load_str = "N/A"
    if hasattr(os, "getloadavg"):
        try:
            load = os.getloadavg()
            load_str = f"{load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}"
        except Exception:
            pass
            
    # 5. Memory Usage (from /proc/meminfo if on Linux)
    mem_str = "N/A"
    try:
        if Path("/proc/meminfo").exists():
            meminfo = Path("/proc/meminfo").read_text()
            total_match = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
            avail_match = re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo)
            if total_match and avail_match:
                total_gb = int(total_match.group(1)) / 1024 / 1024
                avail_gb = int(avail_match.group(1)) / 1024 / 1024
                used_gb = total_gb - avail_gb
                mem_str = f"{used_gb:.1f}GB / {total_gb:.1f}GB used"
    except Exception:
        pass

    # 6. Check local LLM status
    llm_status = "OFFLINE"
    try:
        with httpx.Client(timeout=1.0) as client:
            resp = client.get(f"{LLM_BASE}/models" if "llama" in LLM_BASE or "localhost" in LLM_BASE else f"{LLM_BASE}")
            if resp.status_code in (200, 401, 404):
                llm_status = "ONLINE"
    except Exception:
        pass
        
    hn_stories = fetch_hn_top_stories()
    wiki_article = fetch_random_wikipedia_article()
    
    # Format JARVIS boot screen
    lines = []
    lines.append(co(C.BOLD + C.CYAN, "  🤖 J.A.R.V.I.S. Diagnostics Protocol"))
    lines.append(dim("  " + "─" * 74))
    lines.append(f"  {greeting}")
    lines.append(f"  • {co(C.BOLD, 'Power Source:')}     {battery_status}")
    lines.append(f"  • {co(C.BOLD, 'Load & Memory:')}    {load_str}  ·  {mem_str}")
    lines.append(f"  • {co(C.BOLD, 'Workspace:')}        {git_status}")
    lines.append(f"  • {co(C.BOLD, 'Neural Link:')}      Local LLM Backend: {co(C.GREEN if llm_status == 'ONLINE' else C.RED, llm_status)}")
    lines.append(f"  • {co(C.BOLD, 'Security Grid:')}    Nominal. Encryption active.")
    
    if hn_stories:
        lines.append("")
        lines.append(co(C.BOLD + C.PURPLE, "  📰 Hacker News Intelligence Report:"))
        for idx, story in enumerate(hn_stories):
            lines.append(f"    {idx+1}. {co(C.BOLD, story['title'])}")
            lines.append(dim(f"       {story['url']}"))
            
    if wiki_article:
        lines.append("")
        lines.append(co(C.BOLD + C.PURPLE, f"  📚 Random Wikipedia Article: {wiki_article['title']}"))
        lines.append(dim(f"     {wiki_article['url']}"))
        for w_line in wiki_article["extract_lines"]:
            lines.append(f"     {w_line}")

    lines.append(dim("  " + "─" * 74))
    return "\n".join(lines)


def check_and_summarize_obsidian_vault(force_scan=False, silent=False) -> str:
    """
    Scans the Obsidian Vault for the 3 most recently modified notes,
    and returns a formatted string of their filenames without reading their content.
    If silent=True, suppresses the scanning print-out (used at startup).
    """
    config = config_load()
    vault_path_str = os.environ.get("OBSIDIAN_VAULT") or config.get("obsidian_vault_path")
    if not vault_path_str:
        return ""

    vault_path = Path(vault_path_str).expanduser().resolve()
    if not vault_path.exists():
        return f"Obsidian Vault path does not exist: {vault_path_str}"
    if not vault_path.is_dir():
        return f"Obsidian Vault path is not a directory: {vault_path_str}"

    if not silent:
        print(f"\n{co(C.CYAN, '  🔎 Scanning Obsidian Vault:')} {vault_path}")
    
    # Walk and find .md files
    md_files = []
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.endswith('.md') and not f.startswith('.'):
                md_files.append(Path(root) / f)

    if not md_files:
        return "No markdown notes found in the Obsidian Vault."

    # Sort by mtime descending
    md_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest_files = md_files[:3]

    # Format the insights beautifully listing files only
    lines = []
    lines.append("╭──────────────────────────────────────────────────────────────────────────╮")
    lines.append(f"│  {co(C.BOLD + C.PURPLE, 'OBSIDIAN VAULT INSIGHTS (Latest Modified Notes)')}                     │")
    lines.append("├──────────────────────────────────────────────────────────────────────────┤")
    for idx, fp in enumerate(latest_files):
        rel_path = fp.relative_to(vault_path)
        path_line = f"  • {rel_path}"
        lines.append(f"│ {co(C.BOLD + C.CYAN, path_line.ljust(72))} │")
        if idx < len(latest_files) - 1:
            lines.append("│                                                                          │")
    lines.append("╰──────────────────────────────────────────────────────────────────────────╯")

    insight_str = "\n".join(lines)
    return insight_str


def session_load(session_id: str) -> dict | None:
    """Load a full session from disk."""
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def session_list(limit: int = 20) -> list:
    """List recent sessions (metadata only, no messages)."""
    files = sorted(
        SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    sessions = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text())
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
            data = json.loads(f.read_text())
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
        data = json.loads(path.read_text())
        data["task"] = new_name[:200]
        path.write_text(json.dumps(data, indent=2, default=str))
        return True
    except (json.JSONDecodeError, KeyError):
        return False


def _format_sessions_table(sessions: list) -> str:
    """Render session list as a formatted table with numbered entries."""
    if not sessions:
        return dim("  No sessions found.")
    lines = [f"\n{co(C.BOLD + C.PURPLE, '── SESSION HISTORY ──')}"]
    # Header with # number column
    lines.append(dim(f"  {'#':<4} {'ID':<20} {'MODE':<10} {'MSGS':<6} {'TASK':<50}"))
    lines.append(dim("  " + "─" * 90))
    for i, s in enumerate(sessions, 1):
        sid = s["id"]
        ts_short = sid.replace("ses_", "") if sid.startswith("ses_") else sid
        num_tag = co(C.YELLOW, f"#{i:<2}")
        mode_tag = co(C.CYAN if s["mode"] == "coding" else C.GREEN, s["mode"].ljust(10))
        msg_count = str(s.get("message_count", 0)).ljust(6)
        task = s.get("task", "")[:48]
        lines.append(
            f"  {num_tag} {dim(ts_short.ljust(20))} {mode_tag} {dim(msg_count)} {co(C.WHITE, task[:48])}"
        )
    return "\n".join(lines)


def _render_loaded_messages(msgs: list, count: int = 6):
    """Display the last N messages from a loaded session for context."""
    if not msgs:
        return
    relevant = [m for m in msgs if m.get("role") in ("user", "assistant")][-count:]
    if not relevant:
        return
    print(
        f"\n{co(C.BOLD + C.PURPLE, '── LOADED SESSION HISTORY (Last ' + str(len(relevant)) + ') ──')}"
    )
    for m in relevant:
        role_tag = co(C.CYAN, "YOU:") if m["role"] == "user" else co(C.GREEN, "AI:")
        content = (m.get("content") or "")[:200].replace("\n", " ")
        print(f"  {role_tag} {co(C.WHITE, content)}")
    print(dim("  ── End of loaded history ──"))
    print("")


def _session_prompt_list(sessions: list) -> str | None:
    """Interactive session picker. Returns selected session_id or None."""
    print(_format_sessions_table(sessions))
    print("")
    prompt_text = "  Enter session #, ID, 'search <q>' to filter, or blank to cancel: "
    print(dim(prompt_text), end="")
    sys.stdout.flush()
    try:
        choice = sys.stdin.readline().strip()
    except (KeyboardInterrupt, EOFError):
        return None

    if not choice:
        return None

    # Number selection (either "#3" or bare "3")
    if choice.startswith("#") or choice.isdigit():
        try:
            raw = choice.lstrip("#")
            idx = int(raw) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]["id"]
            ui_info(f"Index out of range (1-{len(sessions)}).")
            return None
        except ValueError:
            pass

    # Full ID match (partial suffix match too for convenience)
    for s in sessions:
        if s["id"] == choice or s["id"].endswith(choice):
            return s["id"]

    # Search shortcut
    if choice.startswith("search ") or choice.startswith("s "):
        q = choice[choice.index(" ") + 1 :]
        results = session_search(q)
        if results:
            return _session_prompt_list(results)
        ui_info(f"No sessions matching '{q}'.")
        return None

    ui_info(f"Session '{choice}' not found.")
    return None


# ── Tool Results Buffer ────────────────────────────────────────────────────────
_tool_results_buffer: list = []


def tool_result_clear():
    _tool_results_buffer.clear()


def tool_result_append(name: str, args: dict, result, error: bool):
    _tool_results_buffer.append(
        {
            "name": name,
            "args": args,
            "result": result,
            "error": error,
            "index": len(_tool_results_buffer),
        }
    )


def tool_result_show(index: int = -1):
    """Re-print a tool result in full detail. Shows last result by default."""
    if not _tool_results_buffer:
        ui_info("No tool results to show.")
        return
    if index < 0 or index >= len(_tool_results_buffer):
        index = len(_tool_results_buffer) - 1
    tr = _tool_results_buffer[index]
    ui_tool_call(tr["name"], tr["args"], tr["result"], tr["error"])


def show_tool_history():
    """Display numbered tool result history."""
    if not _tool_results_buffer:
        ui_info("No tool results recorded in this session.")
        return

    lines = [f"\n{co(C.BOLD + C.CYAN, '── TOOL EXECUTION HISTORY ──')}"]
    lines.append("")
    for tr in _tool_results_buffer:
        status = co(C.GREEN, "✓") if not tr["error"] else co(C.RED, "✖")
        icon = TOOL_ICONS.get(tr["name"], "⚙️ ")
        args_summary = ", ".join(f"{k}={str(v)[:40]}" for k, v in tr["args"].items())
        lines.append(
            f"  [{tr['index']}] {status} {icon} {co(C.BOLD, tr['name'].upper())} {dim(args_summary[:80])}"
        )
    lines.append("")
    lines.append(
        dim(
            "  Use '/tools <index>' to view full details, or Ctrl+O to toggle compact mode."
        )
    )
    print("\n".join(lines))


# ── Event Renderer ─────────────────────────────────────────────────────────────
class ConsoleRenderer:
    """
    Append-only terminal renderer with live-streaming and markdown-lite support.
    """

    def __init__(self):
        self.spinner = None
        self.block_type = None  # 'thinking' or 'assistant'
        self.in_bold = False
        self.in_code_block = False
        self._code_lines = []  # buffer of full lines for current code block
        self._code_line_buf = ""  # current line being accumulated
        self._code_lang = ""  # detected language
        self._code_line_count = 0  # lines written in current code block
        self.line_start = True
        self.header_level = 0
        self.usage = {}
        self.step = 0
        # Markdown formatting state
        self._in_table = False
        self._table_is_separator = False
        self._in_italic = False
        self._list_depth = 0

    def handle(self, event: dict):
        kind = event.get("type")
        if kind == "llm_start":
            self.spinner = Spinner(event.get("label", "Thinking…"))
            self.spinner.start()
        elif kind == "llm_first_output":
            self.stop_spinner()
        elif kind == "reasoning_delta":
            self.ensure_block("thinking")
            self.write_delta(event.get("text", ""))
        elif kind == "assistant_delta":
            self.ensure_block("assistant")
            self.write_delta(event.get("text", ""))
        elif kind == "assistant_done":
            self.usage = event.get("usage", {})
            self.finish_block()
        elif kind == "tool_call_queued":
            self.finish_block()
        elif kind == "tool_call_delta":
            delta_name = event.get("name", "")
            delta_args = event.get("arguments", "")

            self.current_tool_name = getattr(self, "current_tool_name", "") + delta_name
            self.current_tool_args = getattr(self, "current_tool_args", "") + delta_args

            if not self.in_code_block and self.current_tool_name in (
                "write_file",
                "patch_file",
                "run_bash",
                "run_python",
            ):
                if len(self.current_tool_args) > 2:
                    self.ensure_block("assistant")
                    self.in_code_block = True
                    sys.stdout.write(
                        co(
                            C.GRAY,
                            f"\n  ``` {self.current_tool_name.upper()} (Live Stream)\n  ",
                        )
                    )
                    sys.stdout.flush()

            if self.in_code_block:
                # Fast basic unescape for live viewing so the user sees the code
                clean = (
                    delta_args.replace("\\n", "\n  ")
                    .replace("\\t", "    ")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )

                # Strip common JSON boilerplate from the stream chunk
                if self.current_tool_name == "write_file":
                    clean = re.sub(
                        r'^\{?"?path"?:?\s*"[^"]*",?\s*"?content"?:?\s*"', "", clean
                    )
                elif self.current_tool_name == "patch_file":
                    clean = re.sub(
                        r'^\{?"?path"?:?\s*"[^"]*",?\s*"?search"?:?\s*"', "", clean
                    )

                if clean:
                    sys.stdout.write(co(C.GRAY, clean))
                    sys.stdout.flush()

        elif kind == "tool_call_result":
            self.current_tool_name = ""
            self.current_tool_args = ""
            self.finish_block()
            ui_tool_call(
                event.get("name", "tool"),
                event.get("args", {}),
                event.get("result"),
                event.get("error", False),
            )
        elif kind == "usage":
            self.usage = event.get("usage", {})
            self.step = event.get("step", 0)
        elif kind == "error":
            self.stop_spinner()
            ui_error(event.get("message", "Unknown error"))

    def stop_spinner(self):
        if self.spinner:
            self.spinner.stop()
            self.spinner = None

    def ensure_block(self, btype: str):
        if self.block_type == btype:
            return
        self.finish_block()
        self.block_type = btype
        if btype == "thinking":
            sys.stdout.write(f"\n{co(C.GRAY, '  ╭── Thought Process')}\n")
        else:
            sys.stdout.write(f"\n{co(C.CYAN, '  ╭── Assistant')}\n")
        self.line_start = True
        sys.stdout.flush()

    def finish_block(self):
        self.stop_spinner()
        if self.block_type:
            i = self.usage.get("prompt_tokens", 0)
            o = self.usage.get("completion_tokens", 0)
            if i or o:
                sys.stdout.write(dim(f"\n  ⚡ {i} in / {o} out (step {self.step})"))
            sys.stdout.write("\n")
            self.block_type = None
            self.in_code_block = False
        sys.stdout.flush()

    @staticmethod
    def _highlight_code(code: str, lang: str) -> str:
        """Apply pygments syntax highlighting with ANSI codes. Falls back to plain text."""
        if not code.strip():
            return ""
        try:
            from pygments import highlight
            from pygments.lexers import get_lexer_by_name, guess_lexer, PythonLexer
            from pygments.formatters import Terminal256Formatter

            if lang:
                try:
                    lexer = get_lexer_by_name(lang, stripall=True)
                except Exception:
                    lexer = guess_lexer(code)
            else:
                lexer = guess_lexer(code)

            # Remove trailing newline to avoid blank line
            code = code.rstrip("\n")
            return highlight(code, lexer, Terminal256Formatter(style="monokai"))
        except ImportError:
            # No pygments available — use plain ANSI grey as fallback
            return co(C.GRAY, code)
        except Exception:
            return co(C.GRAY, code)

    def write_delta(self, text: str):
        """Streaming character-by-character with stateful formatting and code block support"""
        i = 0
        while i < len(text):
            char = text[i]

            # 1. Newline / Line Start Handling
            if char == "\n":
                sys.stdout.write(C.RST + "\n")
                if self.block_type == "assistant":
                    sys.stdout.write("  ")
                self.line_start = True
                self.header_level = 0
                self.in_bold = False
                self._in_table = False
                self._table_is_separator = False
                self._in_italic = False
                if self.in_code_block:
                    self._code_lines.append(self._code_line_buf)
                    self._code_line_buf = ""
                    self._code_line_count += 1
                i += 1
                sys.stdout.flush()  # CRITICAL: Flush on every newline
                continue

            # 2. Code Block Detection (```)
            if char == "`" and i + 2 < len(text) and text[i : i + 3] == "```":
                if not self.in_code_block:
                    # OPENING
                    self.in_code_block = True
                    self._code_lang = ""
                    self._code_lines = []
                    self._code_line_count = 0
                    i += 3
                    # Extract optional language identifier
                    rest = text[i:]
                    eol = rest.find("\n")
                    if eol >= 0:
                        self._code_lang = rest[:eol].strip()
                        i += eol + 1
                    elif rest:
                        self._code_lang = rest.strip()
                        i = len(text)
                    sys.stdout.write(co(C.GRAY, f"```{self._code_lang}\n"))
                    self._code_line_count += 1
                else:
                    # CLOSING — re-render with syntax highlighting
                    self.in_code_block = False
                    i += 3
                    highlighted = self._highlight_code(
                        "\n".join(self._code_lines), self._code_lang
                    )
                    # Move cursor up to overwrite the grey preview
                    lines_up = self._code_line_count + 1  # +1 for ```lang header
                    for _ in range(lines_up):
                        sys.stdout.write("\033[F\033[K")
                    sys.stdout.write(highlighted)
                    sys.stdout.write(f"\n{co(C.GRAY, '```')}\n")
                    self._code_lines = []
                    self._code_lang = ""
                    self._code_line_count = 0
                sys.stdout.flush()
                continue

            # 3. Markdown Header Handling (Only outside code blocks)
            if not self.in_code_block and self.line_start and char == "#":
                count = 0
                while i < len(text) and text[i] == "#":
                    count += 1
                    i += 1
                self.header_level = count
                sys.stdout.write(co(C.BOLD + C.CYAN, "#" * count))
                sys.stdout.flush()
                continue

            # 4. Bold Toggle Handling (Only outside code blocks)
            if (
                not self.in_code_block
                and char == "*"
                and i + 1 < len(text)
                and text[i + 1] == "*"
            ):
                self.in_bold = not self.in_bold
                i += 2
                sys.stdout.flush()
                continue

            # 4.5. Line-Start Formatting (tables, lists, blockquotes, HR)
            if not self.in_code_block and self.line_start:
                # Bullet list: - text  or  * text
                if char == "-" and i + 1 < len(text) and text[i + 1] == " ":
                    sys.stdout.write(C.WHITE + "•")
                    i += 2
                    self.line_start = False
                    sys.stdout.flush()
                    continue
                if char == "*" and i + 1 < len(text) and text[i + 1] == " ":
                    sys.stdout.write(C.WHITE + "•")
                    i += 2
                    self.line_start = False
                    sys.stdout.flush()
                    continue
                # Blockquote: > text
                if char == ">" and i + 1 < len(text) and text[i + 1] == " ":
                    sys.stdout.write(C.DIM + C.WHITE + "▎")
                    i += 2
                    self.line_start = False
                    sys.stdout.flush()
                    continue
                # Horizontal rule: ---  ***  ___
                if (
                    char in ("-", "*", "_")
                    and i + 2 < len(text)
                    and text[i + 1] == char
                    and text[i + 2] == char
                ):
                    # Assume HR — render entire line as dim ruler
                    # Skip all remaining chars of this line
                    while i < len(text) and text[i] != "\n":
                        i += 1
                    sys.stdout.write(dim("─" * (min(92, 60))))
                    self.line_start = False
                    sys.stdout.flush()
                    continue
                # Table row: | col | col |
                if char == "|":
                    self._in_table = True
                    if i + 1 < len(text) and text[i + 1] == "-":
                        self._table_is_separator = True

            # 4.7. Italic Toggle (single * not at line start, not in bold, not a list bullet)
            if (
                not self.in_code_block
                and char == "*"
                and not self.line_start
                and not (i + 1 < len(text) and text[i + 1] == "*")
                and not (
                    i > 0
                    and text[i - 1] == "*"
                    and i + 1 < len(text)
                    and text[i + 1] != "*"
                )
            ):
                self._in_italic = not self._in_italic
                i += 1
                sys.stdout.flush()
                continue

            # 5. Standard Character Rendering
            if char != " " and self.line_start:
                self.line_start = False

            # Determine Color/Style
            style = C.WHITE
            if self.in_code_block:
                style = C.GRAY
            elif self.block_type == "thinking":
                style = C.DIM + C.GRAY
            elif self._in_italic:
                style = C.DIM + C.WHITE
            elif self._table_is_separator:
                style = C.DIM + C.GRAY
            elif self.header_level > 0:
                style = C.BOLD + (C.PURPLE if self.header_level == 2 else C.YELLOW)
            elif self.in_bold:
                style = C.BOLD + C.WHITE

            # Table: replace | with box-drawing │
            if self._in_table and char == "|":
                char = "│"

            # Buffer code block content for post-hoc highlighting
            if self.in_code_block:
                self._code_line_buf += char

            sys.stdout.write(style + char)
            i += 1
        sys.stdout.flush()  # CRITICAL: Final flush after processing delta


# ── LLM Streaming Client ───────────────────────────────────────────────────────
def iter_chat_events(messages: list, tools: list = None):
    """
    Stream chat completions from the configured provider.
    Yields structured events; rendering happens outside this parser.
    Delegates to providers.iter_chat_events for the actual streaming.
    """
    try:
        yield from providers.iter_chat_events(messages, tools)
    except KeyboardInterrupt:
        yield {"type": "error", "message": "Interrupted by user."}
        raise AgentInterrupted()


def stream_chat(
    messages: list, tools: list = None, renderer: ConsoleRenderer = None
) -> tuple:
    """
    Stream from the configured provider.
    Returns (message_dict, finish_reason, usage). Rendering is event-driven.
    """
    renderer = renderer or ConsoleRenderer()
    final = None
    try:
        for event in iter_chat_events(messages, tools):
            renderer.handle(event)
            if event.get("type") == "assistant_done":
                final = event
    except KeyboardInterrupt:
        raise AgentInterrupted()
    finally:
        renderer.stop_spinner()

    if not final:
        raise RuntimeError("LLM stream ended without a final assistant message")
    return final["message"], final["finish_reason"], final["usage"]


def debug_stream(user_message: str):
    """Print raw stream timing so UI delays can be separated from server delays."""
    url = providers.get_chat_url()
    print(dim(f"POST {url}  [{providers.status_line()}]"))
    started = last = time.monotonic()
    payload = {
        "model": providers.get_model(),
        "messages": [
            {"role": "system", "content": "Reply directly and briefly."},
            {"role": "user", "content": user_message},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": 512,
        "stream": True,
    }
    started = last = time.monotonic()
    with httpx.Client(timeout=180) as client:
        with client.stream(
            "POST", f"{LLM_BASE}/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                now = time.monotonic()
                if not raw_line.startswith("data: "):
                    continue
                data = raw_line[6:].strip()
                if data == "[DONE]":
                    print(dim(f"[{now - started:6.2f}s +{now - last:5.2f}s] DONE"))
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    print(co(C.RED, f"[{now - started:6.2f}s] bad json: {data[:100]}"))
                    last = now
                    continue
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                keys = ",".join(delta.keys()) or "-"
                content = (
                    delta.get("content")
                    or delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("thinking")
                    or ""
                )
                print(
                    dim(
                        f"[{now - started:6.2f}s +{now - last:5.2f}s] keys={keys} text={repr(content[:70])}"
                    )
                )
                last = now


# ── Tool Implementations ───────────────────────────────────────────────────────

# —— File tools ——


def _safe_path(path: str) -> Path:
    """Resolve path relative to current working directory."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def outline_file(path: str) -> str:
    """Return file structure without full content (saves context tokens)"""
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: '{path}' does not exist"

    text = p.read_text(errors="replace")
    lines = text.splitlines()
    total = len(lines)
    out = [f"FILE: {path}  ({total} lines)\n"]

    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    out.append(f"  L{node.lineno:>4}  class {node.name}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = (
                        "async def"
                        if isinstance(node, ast.AsyncFunctionDef)
                        else "def    "
                    )
                    indent = (
                        "    "
                        if any(
                            isinstance(p, ast.ClassDef)
                            and p.lineno < node.lineno < (p.end_lineno or 9999)
                            for p in ast.walk(tree)
                        )
                        else ""
                    )
                    out.append(f"  L{node.lineno:>4}  {indent}{kind} {node.name}()")
        except SyntaxError as e:
            out.append(f"  [SyntaxError at line {e.lineno}: {e.msg}]")
    elif path.endswith((".json", ".yaml", ".yml", ".toml")):
        out.append("  [config file — use read_file_section to inspect]")
    else:
        out.append("  [non-Python file]")

    # Always show first 8 lines as preview
    out.append("\nPREVIEW (first 8 lines):")
    out.extend(f"  {i + 1:>4}: {l}" for i, l in enumerate(lines[:8]))
    if total > 8:
        out.append(f"  ... ({total - 8} more lines)")

    return "\n".join(out)


def read_file_section(path: str, start_line: int, end_line: int) -> str:
    """Read specific line range (1-indexed)"""
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: '{path}' does not exist"

    ACTIVE_FILES.add(str(p.absolute()))
    lines = p.read_text(errors="replace").splitlines()

    total = len(lines)
    s = max(0, start_line - 1)
    e = min(total, end_line)

    out = [f"[{path}  lines {start_line}–{end_line} of {total}]", ""]
    out += [f"{s + i + 1:>5}: {l}" for i, l in enumerate(lines[s:e])]
    return "\n".join(out)


def write_file(path: str, content: str) -> str:
    """Write full content to a file (use patch_file for edits)"""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    AGENT_MODIFIED_FILES[str(p.absolute())] = time.time()
    ACTIVE_FILES.add(str(p.absolute()))
    lines = content.count("\n") + 1
    return f"Written: {path}  ({lines} lines)"


from web_search import (
    search_web,
    web_fetch,
    scout_website,
    search_second_brain,
)


def patch_file(path: str, search: str, replace: str) -> str:
    """
    Find-and-replace in a file. Fails loudly if search string not found.
    Use read_file_section first to get exact text.
    """
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: '{path}' does not exist"

    original = p.read_text()
    occurrences = original.count(search)

    if occurrences == 0:
        # Give a helpful hint
        first_line = search.strip().splitlines()[0][:60] if search.strip() else ""
        return (
            f"ERROR: Search string not found in '{path}'.\n"
            f"Looked for: {repr(first_line)}...\n"
            f"Use read_file_section() to get exact text and try again."
        )
    if occurrences > 1:
        return (
            f"ERROR: Search string found {occurrences} times in '{path}' — "
            f"must be unique. Add more context lines to make it unique."
        )

    updated = original.replace(search, replace, 1)
    p.write_text(updated)
    AGENT_MODIFIED_FILES[str(p.absolute())] = time.time()
    ACTIVE_FILES.add(str(p.absolute()))

    old_n = original.count("\n") + 1
    new_n = updated.count("\n") + 1
    delta = f"+{new_n - old_n}" if new_n >= old_n else str(new_n - old_n)

    # Build a red/green diff summary of the changed region
    old_lines = original.splitlines()
    new_lines = updated.splitlines()
    search_lines = search.strip().splitlines() if search.strip() else []
    replace_lines = replace.strip().splitlines() if replace.strip() else []
    diff_lines = []

    # Show old lines (removed) in red, new lines (added) in green
    # Use the search temAM as the "old" block to highlight
    for ln in search_lines:
        diff_lines.append(f"{co(C.RED, '  -' + ln)}")
    for ln in replace_lines:
        diff_lines.append(f"{co(C.GREEN, '  +' + ln)}")

    diff_output = "\n".join(diff_lines[:30])
    if len(diff_lines) > 30:
        diff_output += f"\n{co(C.GRAY, '  ... ' + str(len(diff_lines) - 30) + ' more lines')}"

    return f"Patched: {path}  ({old_n} → {new_n} lines, {delta})\n{diff_output}"


def grep_codebase(pattern: str, path: str = ".", file_ext: str = ".py") -> str:
    """Regex search across all files with a given extension"""
    root = Path(path)
    matches = []
    SKIP = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache"}

    for f in root.rglob(f"*{file_ext}"):
        if any(part in SKIP for part in f.parts):
            continue
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if re.search(pattern, line):
                    matches.append(f"{f}:{i}: {line.strip()[:100]}")
        except Exception:
            continue

    if not matches:
        return f"No matches for pattern '{pattern}' in *{file_ext} files under '{path}'"

    out = "\n".join(matches[:60])
    if len(matches) > 60:
        out += f"\n… ({len(matches) - 60} more results)"
    return out


def graph_search(query: str, path: str = ".", file_ext: str = ".py") -> str:
    """
    Fast local symbol search for small-context models.
    Builds a lightweight AST view on demand instead of dumping whole files.
    """
    root = Path(path)
    q = query.lower()
    hits = []
    SKIP = {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
    }

    for f in root.rglob(f"*{file_ext}"):
        if any(part in SKIP for part in f.parts):
            continue
        try:
            text = f.read_text(errors="replace")
            tree = ast.parse(text)
        except Exception:
            continue

        parents = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                parents.append(
                    (node.lineno, getattr(node, "end_lineno", node.lineno), node.name)
                )

        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                if q not in name.lower() and q not in f.as_posix().lower():
                    continue
                owner = ""
                if kind == "function":
                    for start, end, cls_name in parents:
                        if start < node.lineno <= end:
                            owner = f"{cls_name}."
                            break
                hits.append(f"{f}:{node.lineno}: {kind} {owner}{name}")

    if not hits:
        return f"No graph symbols found for '{query}' under '{path}'"
    out = "\n".join(hits[:80])
    if len(hits) > 80:
        out += f"\n… ({len(hits) - 80} more symbols)"
    return out


def sentinel_map_codebase() -> str:
    """
    Architect-Sentinel: Automated codebase mapping.
    Scans the project to build a structural 'Global Blueprint'.
    Includes personal context and dynamic skill suggestions.
    """
    cwd = Path.cwd()
    python_files = list(cwd.glob("**/*.py"))
    js_files = list(cwd.glob("**/*.{js,ts,jsx,tsx}"))
    html_files = list(cwd.glob("**/*.html"))

    parts = ["--- ARCHITECT-SENTINEL GLOBAL BLUEPRINT ---"]

    # Inject User Biography if exists
    bio_path = cwd / "memory" / "BIOGRAPHY.md"
    if bio_path.exists():
        bio_content = bio_path.read_text().strip()
        if bio_content:
            parts.append("\n👤 USER CONTEXT (BIOGRAPHY):")
            parts.append(trunc(bio_content, 1000))
            parts.append("----------------------------\n")

    parts.append(f"Project Root: {cwd}")

    # Detect project type
    project_type = "Unknown"
    if (cwd / "setup.py").exists() or (cwd / "pyproject.toml").exists():
        project_type = "Python Package"
    elif (cwd / "package.json").exists():
        project_type = "Node.js"
    elif (cwd / "Cargo.toml").exists():
        project_type = "Rust"
    elif (cwd / "go.mod").exists():
        project_type = "Go"
    elif (cwd / "Makefile").exists() and (cwd / "Dockerfile").exists():
        project_type = "DevOps/Infra"
    parts.append(f"Project Type: {project_type}")
    parts.append(f"Composition: {len(python_files)} Python, {len(js_files)} JS/TS, {len(html_files)} HTML files.")

    # Detect frameworks/libs from imports
    all_py_text = ""
    for f in python_files[:20]:
        try:
            all_py_text += f.read_text(errors="ignore").lower() + "\n"
        except Exception:
            pass

    frameworks = []
    if "flask" in all_py_text:
        frameworks.append("Flask")
    if "fastapi" in all_py_text:
        frameworks.append("FastAPI")
    if "django" in all_py_text:
        frameworks.append("Django")
    if "pytest" in all_py_text:
        frameworks.append("pytest")
    if "tensorflow" in all_py_text or "keras" in all_py_text:
        frameworks.append("TensorFlow/Keras")
    if "torch" in all_py_text:
        frameworks.append("PyTorch")
    if "transformers" in all_py_text:
        frameworks.append("HuggingFace Transformers")
    if "playwright" in all_py_text or "selenium" in all_py_text:
        frameworks.append("Browser Automation")
    if "httpx" in all_py_text or "requests" in all_py_text:
        frameworks.append("HTTP Client (httpx/requests)")

    if frameworks:
        parts.append(f"Detected Frameworks: {', '.join(frameworks)}")

    # Dynamic Skill Suggestions
    skills = []
    if any(cwd.glob("**/*.html")) or any(cwd.glob("**/*.jsx")):
        skills.append("Frontend-Development")
    if "fastapi" in all_py_text or "flask" in all_py_text:
        skills.append("API-Development")
    if any(cwd.glob("**/docker-compose.yml")) or (cwd / "Dockerfile").exists():
        skills.append("Docker-Orchestration")
    if any(cwd.glob("**/*.tf")) or any(cwd.glob("**/helm/")):
        skills.append("Infrastructure-as-Code")
    if any(cwd.glob("**/*.{yml,yaml}")) and any(cwd.glob("**/*.py")):
        skills.append("CI/CD-Pipeline")
    if "pytest" in all_py_text or any(cwd.glob("**/test_*.py")):
        skills.append("Python-Testing")
    if "torch" in all_py_text or "tensorflow" in all_py_text:
        skills.append("ML-Model-Training")

    if skills:
        parts.append("\n💡 SUGGESTED SKILLS (Load with `load_skill`):")
        for s in skills:
            parts.append(f"- {s}")
        parts.append("")

    # Map top-level structure
    for p in sorted(cwd.glob("*")):
        if p.name.startswith(".") or "venv" in p.name or p.name == "__pycache__":
            continue
        if p.is_dir():
            sub = [f.name for f in p.glob("*") if not f.name.startswith(".") and f.name != "__pycache__"]
            parts.append(
                f"📁 {p.name}/: {', '.join(sub[:10])}{'...' if len(sub) > 10 else ''}"
            )
        else:
            parts.append(f"📄 {p.name}")

    # Extract key symbols from main loop or index — limit to avoid token bloat
    if Path("loop.py").exists():
        parts.append("\nCore Logic (loop.py) symbols:")
        parts.append(outline_file("loop.py"))

    return "\n".join(parts)


def skill_factory(task_name: str, pattern_description: str) -> str:
    """
    Self-Improvement: Records a successful information-gathering pattern into a new built-in skill.
    """
    skill_name = re.sub(r"[^a-zA-Z0-9]", "_", task_name).lower()
    path = Path(f"skills/{skill_name}.md")
    path.parent.mkdir(exist_ok=True)

    content = f"""# Skill: {task_name}

## Pattern:
{pattern_description}

## Automated Trigger:
This skill should be applied whenever a task involves {task_name}.

## Implementation Steps:
1. Research using search_web with variants focused on {task_name}.
2. Use scout_website for deep documentation extraction.
3. Apply patterns found in memory related to this task.
"""
    path.write_text(content)
    memory_save(f"Created new skill: {task_name} at {path}", kind="skill_creation")
    return f"SUCCESS: Skill '{task_name}' factory-built at {path}. Use load_skill to apply it."


def consolidate_goals() -> str:
    """
    The Consolidator: Proactively scans memory for user worries/goals and triggers deep research.
    """
    worries = memory_load(
        "worry goal need earn money weight routine health finance", max_results=10
    )
    if worries == "[]":
        return "Consolidator: No urgent goals or worries found in recent memory."

    # Trigger deep research on the most recent/relevant goal
    goal_data = json.loads(worries)[0]
    goal_text = goal_data.get("note", "")

    res = search_web(
        f"deep research comprehensive guide for {goal_text}", max_results=5
    )

    summary = f"""--- CONSOLIDATOR PROACTIVE REPORT ---
I've recognized your goal: "{trunc(goal_text, 100)}"
I performed deep research to formulate a proactive response.

{res}

Would you like me to formulate a daily routine based on this research?
"""
    return summary


# —— Search ——


def verify_syntax(path: str) -> str:
    p = _safe_path(path)
    if p.suffix == ".py":
        try:
            subprocess.check_output(
                ["python", "-m", "py_compile", path], stderr=subprocess.STDOUT
            )
            return "Syntax OK."
        except subprocess.CalledProcessError as e:
            return f"SYNTAX ERROR:\n{e.output.decode()}"
    elif p.suffix in (".js", ".ts", ".html"):
        # For simple HTML/JS without eslint, use ast parsing or simple check if Node is available
        try:
            # Check if node is available
            subprocess.check_output(["node", "-c", path], stderr=subprocess.STDOUT)
            return "Syntax OK."
        except FileNotFoundError:
            return "No Node.js installed to check JS syntax. Manual observation recommended."
        except subprocess.CalledProcessError as e:
            return f"SYNTAX ERROR:\n{e.output.decode()}"
    return "No linter available for this file type."


def summarize_progress(summary: str) -> str:
    """Save a state-of-the-mission report to Shadow Context."""
    Path(".mission_state.txt").write_text(summary)
    return "Shadow Context updated successfully."


# —— Memory ──


def _memory_records() -> list:
    return memory.records()


def memory_load(query: str, max_results: int = 5) -> str:
    """Keyword-search persistent local memory."""
    return memory.load(query, max_results)


def memory_save(note: str, kind: str = "note") -> str:
    """Append a local memory note and record to history for human readability."""
    return memory.save(note, kind)


def auto_memory_context(user_message: str) -> str:
    return memory.auto_context(user_message)


# —— TODO list ——


def todo_read() -> str:
    if not Path(TODO_FILE).exists():
        return "[]"
    return Path(TODO_FILE).read_text()


def todo_write(todos_json: str) -> str:
    try:
        todos = json.loads(todos_json)
        Path(TODO_FILE).write_text(json.dumps(todos, indent=2))
        return f"Saved {len(todos)} todo(s) to {TODO_FILE}"
    except json.JSONDecodeError as e:
        return f"ERROR: Invalid JSON — {e}"


def todo_update(item_id: str, status: str) -> str:
    if not Path(TODO_FILE).exists():
        return f"ERROR: No todo file at {TODO_FILE}"
    todos = json.loads(Path(TODO_FILE).read_text())
    for t in todos:
        if str(t.get("id")) == str(item_id):
            t["status"] = status
            t["updated_at"] = datetime.now().isoformat(timespec="seconds")
            Path(TODO_FILE).write_text(json.dumps(todos, indent=2))
            return f"Todo {item_id} → {status}"
    return f"ERROR: Todo id='{item_id}' not found"


# —— Git ——


def git_status(path: str = ".") -> str:
    """Show git status + recent log"""
    try:
        status = subprocess.check_output(
            ["git", "status", "--short"], cwd=path, text=True, timeout=5
        )
        log = subprocess.check_output(
            ["git", "log", "--oneline", "-8"], cwd=path, text=True, timeout=5
        )
        return f"STATUS:\n{status or '(clean)'}\nRECENT COMMITS:\n{log}"
    except FileNotFoundError:
        return "ERROR: git not found"
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e}"


def run_bash(command: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def run_python(code: str, timeout: int = 30) -> str:
    fpath = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            fpath = f.name
        result = subprocess.run(
            [sys.executable, fpath],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"ERROR: Execution timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        if fpath:
            try:
                os.unlink(fpath)
            except OSError:
                pass


def load_skill(path_or_url: str) -> str:
    try:
        if path_or_url.startswith(("http://", "https://")):
            resp = httpx.get(path_or_url, timeout=30)
            resp.raise_for_status()
            content = resp.text
        else:
            p = Path(path_or_url)
            if not p.exists():
                return f"ERROR: Skill file not found at '{path_or_url}'"
            content = p.read_text()
        skills_dir = Path("skills")
        skills_dir.mkdir(exist_ok=True)
        if path_or_url.startswith(("http://", "https://")):
            name = path_or_url.rstrip("/").split("/")[-1] or "skill.md"
        else:
            name = Path(path_or_url).name
        dest = skills_dir / name
        dest.write_text(content)
        return f"SUCCESS: Skill loaded from '{path_or_url}' → '{dest}'"
    except httpx.HTTPStatusError as e:
        return f"ERROR: HTTP {e.response.status_code}: {e.response.text[:200]}"
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to '{path_or_url}'"
    except Exception as e:
        return f"ERROR: {e}"


# ── Tool Definitions (OpenAI function-call format) ─────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "outline_file",
            "description": (
                "Get the structure of a file (classes, function names, line numbers) "
                "WITHOUT loading its full content. Always call this BEFORE read_file_section "
                "to find the right line range. Saves context tokens."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute file path",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_section",
            "description": (
                "Read a specific range of lines from a file. "
                "Use outline_file first to find start_line and end_line. "
                "Prefer small ranges (20-50 lines) to keep context lean."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {
                        "type": "integer",
                        "description": "First line (1-indexed)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line (inclusive)",
                    },
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write full content to a file. "
                "Use ONLY for creating NEW files. "
                "For editing existing files use patch_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "Full file content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "Find and replace an exact string in a file. "
                "PREFERRED for all edits — never rewrites the whole file. "
                "The search string must be unique in the file. "
                "Always include a few surrounding lines to make it unique. "
                "If it fails, use read_file_section to get the exact text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "search": {
                        "type": "string",
                        "description": "Exact text to find (must be unique)",
                    },
                    "replace": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "search", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_codebase",
            "description": "Search for a regex pattern across files in the codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {
                        "type": "string",
                        "description": "Root dir to search (default '.')",
                    },
                    "file_ext": {
                        "type": "string",
                        "description": "Extension to match (default '.py')",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_search",
            "description": (
                "Fast symbol-level search over the local code graph. "
                "Use before reading files when looking for functions, classes, methods, or modules."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Function, class, method, or filename query",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root dir to search (default '.')",
                    },
                    "file_ext": {
                        "type": "string",
                        "description": "Extension to match (default '.py')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web via SearXNG. Use for: docs, error messages, "
                "library APIs, current events, anything you're unsure about."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "description": "Results to return (default 8)",
                    },
                    "current": {
                        "type": "boolean",
                        "description": "Add current-year/latest/code-example query variants (default true)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "Fetch and activate a modular skill (SKILL.md) from GitHub or a local path. "
                "Use this to gain specialized knowledge for frameworks, languages, or security."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_url": {
                        "type": "string",
                        "description": "URL or local path to the skill file",
                    }
                },
                "required": ["path_or_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": (
                "Run a bash command in the current workspace. "
                "Use for tests, git, build tools, shell inspection, and commands the user explicitly asks for."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": f"Max seconds (default {PYTHON_TIMEOUT})",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute Python in a sandboxed subprocess. "
                "Use for: calculations, API requests, data processing, testing code logic, "
                "installing packages, reading CSVs, anything a terminal can do. "
                "ALWAYS test code here before writing it to a file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to run"},
                    "timeout": {
                        "type": "integer",
                        "description": f"Max seconds (default {PYTHON_TIMEOUT})",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_load",
            "description": "Retrieve relevant persistent local memory. Use at the start when personal/project preferences may matter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "description": "Results to return (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": "Save a durable local memory note about user preferences, project facts, or agent lessons learned.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "description": "note, preference, project, lesson, or session",
                    },
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "Read the current TODO list for this task.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": (
                "Write the full TODO list. Call at the START of any coding task. "
                "Each item: {id, title, priority (HIGH/MEDIUM/LOW), status, notes}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos_json": {
                        "type": "string",
                        "description": (
                            "JSON array. Example: "
                            '[{"id":"1","title":"Add error handling","priority":"HIGH",'
                            '"status":"pending","notes":"in utils.py"}]'
                        ),
                    }
                },
                "required": ["todos_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_update",
            "description": "Update a single TODO item's status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "The id field of the todo",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "failed"],
                    },
                },
                "required": ["item_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_second_brain",
            "description": "Search your local 'Second Brain' which automatically stores all previously fetched web pages and docs. Always check this before doing a web_fetch for previously researched topics to save time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find in your local knowledge base.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch the full text content of a URL. Now supports a 'query' parameter to automatically extract only the most relevant RAG chunks instead of returning the whole bloated page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch."},
                    "query": {
                        "type": "string",
                        "description": "Optional search query. If provided, returns a small extracted Knowledge Graph containing only sentences highly relevant to the query.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scout_website",
            "description": "Fetch a URL and automatically extract and fetch top internal links. Best for deep API context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "depth": {
                        "type": "integer",
                        "description": "1 to fetch sub-links, 0 for just the main page (default 1).",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_syntax",
            "description": "Run a fast syntax check on a file before making more changes. Prevents hallucination drift.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to check."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_progress",
            "description": "Save a state-of-the-mission report to Shadow Context. Call this periodically on long tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Concise summary of current architecture, completed tasks, and next steps.",
                    }
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sentinel_map_codebase",
            "description": "Architect-Sentinel: Automated codebase mapping to prevent architectural drift. Run this at the start of any new mission.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_factory",
            "description": "Self-Improvement: Records a successful information-gathering pattern into a new built-in skill for future reuse.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_name": {
                        "type": "string",
                        "description": "Short name for the repeating task (e.g., 'ThreeJS_Scouting')",
                    },
                    "pattern_description": {
                        "type": "string",
                        "description": "Detailed description of the research and execution steps that worked.",
                    },
                },
                "required": ["task_name", "pattern_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consolidate_goals",
            "description": "The Consolidator: Proactively scans memory for user goals/worries (money, health, etc.) and triggers deep research.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git status and recent commit log for a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repo directory (default '.')"
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smart_search",
            "description": "Generate multiple targeted search queries, run them all in parallel, and aggregate deduplicated results. Use for broad research topics where a single query may miss important angles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The research topic or question"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of search queries to generate (default 3)"
                    }
                },
                "required": ["topic"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tail_file",
            "description": "Read the last N lines of a file. Useful for checking recent log entries, seeing where a truncated write_file stopped, or inspecting the end of a file without reading the whole thing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path"
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of lines to show from the end (default 20)"
                    }
                },
                "required": ["path"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert_lines",
            "description": "Insert content at a specific line number in a file. Shifts existing lines down. More token-efficient than patch_file for adding new blocks at known positions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path"
                    },
                    "line_number": {
                        "type": "integer",
                        "description": "Line number to insert at (1-indexed)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to insert"
                    }
                },
                "required": ["path", "line_number", "content"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_lines",
            "description": "Delete a range of lines from a file (inclusive). More token-efficient than patch_file for removing known line ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to delete (1-indexed)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to delete (inclusive)"
                    }
                },
                "required": ["path", "start_line", "end_line"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": "Replace a range of lines (inclusive) with new content. More token-efficient than patch_file for replacing known line ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to replace (1-indexed)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to replace (inclusive)"
                    },
                    "content": {
                        "type": "string",
                        "description": "New content for the replacement"
                    }
                },
                "required": ["path", "start_line", "end_line", "content"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_code",
            "description": "Validate a file's syntax with detailed diagnostics. Supports Python, JavaScript, HTML, and JSON files. Run after writing or editing code to catch syntax errors early.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to validate"
                    }
                },
                "required": ["path"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_write",
            "description": "Append or complete a file that was truncated during write_file. Scans the last lines and intelligently completes the content. Use when you hit token limits mid-write.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path"
                    },
                    "content": {
                        "type": "string",
                        "description": "New content to append or complete with"
                    }
                },
                "required": ["path", "content"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_analyze_and_improve",
            "description": "Analyse recent conversation and improve the Out-of-the-Box context layer. Updates mission, objectives, and critical user info automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "messages_snapshot": {
                        "type": "string",
                        "description": "A snapshot of recent conversation messages to analyse"
                    }
                },
                "required": ["messages_snapshot"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_update_objective",
            "description": "Update the status of an active objective in the Out-of-the-Box context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the objective to update"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "failed"],
                        "description": "New status (default in_progress)"
                    }
                },
                "required": ["title"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_add_info",
            "description": "Store a critical user fact into the Out-of-the-Box context layer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "Important fact about the user to remember"
                    }
                },
                "required": ["fact"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_mission",
            "description": "Set or update the overarching mission statement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "statement": {
                        "type": "string",
                        "description": "The new mission statement"
                    }
                },
                "required": ["statement"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_search",
            "description": "Search YouTube for a query and return metadata of matching videos (without downloading).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 5)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_fetch_transcript",
            "description": "Fetch the English transcript of a YouTube video using its video ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "video_id": {
                        "type": "string",
                        "description": "The alphanumeric YouTube video ID"
                    }
                },
                "required": ["video_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": "Download audio from YouTube and play it offline using a subprocess (mpv). Will play until user interrupts with Ctrl+C.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_or_url": {
                        "type": "string",
                        "description": "YouTube video URL or search query for the song"
                    }
                },
                "required": ["query_or_url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browse_web",
            "description": "Browse or scrape a website using Playwright. Supports: 'scrape' (inner text matching selector), 'click' (click element), and 'fill' (inputs value and presses Enter).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Website URL to load"
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to target/interact with (default 'body')"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["scrape", "click", "fill"],
                        "description": "Action to perform: 'scrape', 'click', or 'fill' (default 'scrape')"
                    },
                    "value": {
                        "type": "string",
                        "description": "The input text value (required only for 'fill')"
                    }
                },
                "required": ["url"]
            }
        }
    },
]

# ── Smart Search & Novel Tools ────────────────────────────────────────────────


def _quick_llm_call(prompt: str, max_tokens: int = 200) -> str:
    """Simple synchronous LLM call for lightweight sub-tasks."""
    return providers.quick_call(prompt, max_tokens)


def generate_search_queries(topic: str, count: int = 3) -> list:
    """Use the LLM to generate multiple targeted search queries for a topic."""
    prompt = (
        f"Generate {count} specific, targeted web search queries for the topic:\n"
        f"'{topic}'\n\n"
        f"Each query should explore a different angle. Return one query per line, "
        f"each prefixed with 'Q:'. Keep queries concise (5-10 words)."
    )
    response = _quick_llm_call(prompt, max_tokens=300)
    queries = []
    for line in response.splitlines():
        line = line.strip()
        if line.startswith("Q:"):
            q = line[2:].strip().strip('"').strip("'")
            if q and len(q) > 5:
                queries.append(q)
    return queries[:count] if queries else [topic]


def smart_search(topic: str, count: int = 3) -> str:
    """
    TOOL: Generate multiple targeted search queries, run them all,
    and aggregate deduplicated results.
    Returns richer context than a single-shot search.
    """
    queries = generate_search_queries(topic, count)
    if not queries:
        queries = [topic]

    all_results = []
    seen_urls = set()

    for q in queries:
        try:
            raw = search_web(q, max_results=4, current=True)
            # Parse results from search_web output format
            lines = raw.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i]
                # Match result lines: "1. Title"
                match = re.match(r"^(\d+)\.\s+(.+)", line)
                if match:
                    title = match.group(2)
                    url_line = lines[i + 1] if i + 1 < len(lines) else ""
                    snippet_line = lines[i + 2] if i + 2 < len(lines) else ""
                    url = (
                        url_line.replace("   URL: ", "").strip()
                        if "URL:" in url_line
                        else ""
                    )
                    snippet = (
                        snippet_line.replace("   SNIPPET: ", "").strip()
                        if "SNIPPET:" in snippet_line
                        else ""
                    )
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(
                            {"title": title, "url": url, "snippet": snippet}
                        )
                i += 1
        except Exception:
            continue

    if not all_results:
        # Fallback to a single search
        raw = search_web(topic, max_results=6, current=True)
        return raw

    output = [f"SMART SEARCH: {topic}"]
    for r in all_results[:12]:
        output.append(f"\n{r['title']}")
        output.append(f"  URL: {r['url']}")
        if r["snippet"]:
            output.append(f"  {r['snippet'][:250]}")

    return "\n".join(output)


# ── File Inspection & Repair Tools ─────────────────────────────────────────────


def tail_file(path: str, n: int = 20) -> str:
    """TOOL: Read the last N lines of a file. Helps resume interrupted writes."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"
    try:
        lines = p.read_text().splitlines()
        tail = lines[-n:] if len(lines) > n else lines
        total = len(lines)
        start_line = max(1, total - n + 1)
        result = [f"{path} ({total} lines total, showing last {len(tail)}):"]
        for i, line in enumerate(tail, start=start_line):
            result.append(f"{i:>6}: {line}")
        return "\n".join(result)
    except Exception as e:
        return f"ERROR reading file: {e}"


def insert_lines(path: str, line_number: int, content: str) -> str:
    """TOOL: Insert content at a specific line number. Shifts existing lines down."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"
    try:
        lines = p.read_text().splitlines()
        new_lines = content.splitlines()
        idx = max(0, min(line_number - 1, len(lines)))
        lines[idx:idx] = new_lines
        p.write_text("\n".join(lines))
        return f"Inserted {len(new_lines)} line(s) at line {line_number}. File now has {len(lines)} lines."
    except Exception as e:
        return f"ERROR: {e}"


def delete_lines(path: str, start_line: int, end_line: int) -> str:
    """TOOL: Delete a range of lines (inclusive)."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"
    try:
        lines = p.read_text().splitlines()
        if start_line < 1 or end_line > len(lines):
            return f"ERROR: Line range {start_line}-{end_line} out of bounds (file has {len(lines)} lines)"
        removed = lines[start_line - 1 : end_line]
        del lines[start_line - 1 : end_line]
        p.write_text("\n".join(lines))
        return (
            f"Deleted {len(removed)} line(s) (lines {start_line}-{end_line}). "
            f"File now has {len(lines)} lines."
        )
    except Exception as e:
        return f"ERROR: {e}"


def replace_lines(path: str, start_line: int, end_line: int, content: str) -> str:
    """TOOL: Replace a range of lines (inclusive) with new content."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"
    try:
        lines = p.read_text().splitlines()
        if start_line < 1 or end_line > len(lines):
            return f"ERROR: Line range {start_line}-{end_line} out of bounds (file has {len(lines)} lines)"
        new_lines = content.splitlines()
        lines[start_line - 1 : end_line] = new_lines
        p.write_text("\n".join(lines))
        return (
            f"Replaced lines {start_line}-{end_line} with {len(new_lines)} line(s). "
            f"File now has {len(lines)} lines."
        )
    except Exception as e:
        return f"ERROR: {e}"


def validate_code(path: str) -> str:
    """TOOL: Validate a file's syntax with detailed diagnostics. Supports Python, JS, HTML."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"

    ext = p.suffix.lower()
    content = p.read_text()
    issues = []

    if ext == ".py":
        # Python: ast parse
        try:
            ast.parse(content)
            issues.append("✓ Python syntax: VALID")
        except SyntaxError as e:
            issues.append(f"✗ Python syntax error (line {e.lineno}): {e.msg}")
            issues.append(f"  Text: {e.text}")
        # Check for common issues
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            if len(line) > 200:
                issues.append(f"  ⚠ Line {i}: Very long line ({len(line)} chars)")
            if "import *" in line:
                issues.append(f"  ⚠ Line {i}: Wildcard import")
    elif ext == ".js":
        try:
            subprocess.run(
                ["node", "-c", path], capture_output=True, text=True, timeout=10
            )
            issues.append("✓ JavaScript syntax: VALID")
        except FileNotFoundError:
            issues.append("⚠ No Node.js available to validate JS")
        except subprocess.TimeoutExpired:
            issues.append("⚠ JS validation timed out")
        except subprocess.CalledProcessError as e:
            issues.append(f"✗ JavaScript syntax error:\n{e.stderr}")
    elif ext in (".html", ".htm"):
        # Basic HTML validation: check tag balance
        open_tags = re.findall(r"<(\w+)[^>]*>", content)
        close_tags = re.findall(r"</(\w+)>", content)
        from collections import Counter

        open_count = Counter(open_tags)
        close_count = Counter(close_tags)
        for tag in set(list(open_count.keys()) + list(close_tags)):
            if tag in ("br", "hr", "img", "input", "meta", "link"):
                continue
            diff = open_count.get(tag, 0) - close_count.get(tag, 0)
            if diff > 0:
                issues.append(f"  ⚠ <{tag}>: {diff} more opening than closing tags")
            elif diff < 0:
                issues.append(f"  ⚠ <{tag}>: {-diff} more closing than opening tags")
        if not issues:
            issues.append("✓ HTML tags appear balanced")
        # Check for truncated file (no closing html tag)
        if "</html>" not in content.lower():
            issues.append("⚠ File may be truncated: missing </html> tag")
    elif ext == ".json":
        try:
            json.loads(content)
            issues.append("✓ JSON syntax: VALID")
        except json.JSONDecodeError as e:
            issues.append(f"✗ JSON error (line {e.lineno}): {e.msg}")
    else:
        issues.append(f"⚠ No validator configured for {ext} files")

    return "\n".join(issues)


def resume_write(path: str, new_content: str) -> str:
    """
    TOOL: Append or complete a file that was truncated during write_file.
    Scans the last 50 lines, detects where the content was cut off,
    and intelligently completes it.
    """
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}. Use write_file first."

    try:
        current = p.read_text()

        # Check if new content starts with content already in file
        # (deduplication guard)
        overlap = 0
        new_lines = new_content.splitlines()
        existing_lines = current.splitlines()

        # Find where new content continues from existing
        # Check last few lines of existing against first few of new
        for overlap_len in range(min(10, len(existing_lines)), 0, -1):
            tail = existing_lines[-overlap_len:]
            head = new_lines[:overlap_len]
            if tail == head:
                overlap = overlap_len
                break

        if overlap > 0:
            append_content = "\n".join(new_lines[overlap:])
        else:
            append_content = new_content

        # If new content ends abruptly (no newline, no closing tag), flag it
        if append_content and not any(
            append_content.rstrip().endswith(s)
            for s in [">", "}", ";", "```", '"', "')"]
        ):
            p.write_text(current + "\n" + append_content)
            return (
                f"Appended {len(append_content)} chars. "
                f"WARNING: New content may also be truncated (no clear ending)."
            )
        else:
            p.write_text(current + "\n" + append_content)
            return (
                f"Resumed writing to {path}. "
                f"Added {len(append_content)} chars after dedup overlap of {overlap} lines."
            )
    except Exception as e:
        return f"ERROR: {e}"


# Tool dispatcher
TOOL_MAP = {
    "outline_file": lambda a: outline_file(a["path"]),
    "read_file_section": lambda a: read_file_section(
        a["path"], a["start_line"], a["end_line"]
    ),
    "write_file": lambda a: write_file(a["path"], a["content"]),
    "patch_file": lambda a: patch_file(a["path"], a["search"], a["replace"]),
    "grep_codebase": lambda a: grep_codebase(
        a["pattern"], a.get("path", "."), a.get("file_ext", ".py")
    ),
    "graph_search": lambda a: graph_search(
        a["query"], a.get("path", "."), a.get("file_ext", ".py")
    ),
    "search_web": lambda a: search_web(
        a["query"], int(a.get("max_results", 8)), bool(a.get("current", True))
    ),
    "web_fetch": lambda a: web_fetch(a["url"], a.get("query", "")),
    "search_second_brain": lambda a: search_second_brain(a["query"]),
    "scout_website": lambda a: scout_website(a["url"], int(a.get("depth", 1))),
    "verify_syntax": lambda a: verify_syntax(a["path"]),
    "summarize_progress": lambda a: summarize_progress(a["summary"]),
    "run_bash": lambda a: run_bash(a["command"], int(a.get("timeout", PYTHON_TIMEOUT))),
    "run_python": lambda a: run_python(
        a["code"], int(a.get("timeout", PYTHON_TIMEOUT))
    ),
    "memory_load": lambda a: memory_load(a["query"], int(a.get("max_results", 5))),
    "memory_save": lambda a: memory_save(a["note"], a.get("kind", "note")),
    "todo_read": lambda _: todo_read(),
    "todo_write": lambda a: todo_write(a["todos_json"]),
    "todo_update": lambda a: todo_update(a["item_id"], a["status"]),
    "git_status": lambda a: git_status(a.get("path", ".")),
    "load_skill": lambda a: load_skill(a["path_or_url"]),
    "sentinel_map_codebase": lambda _: sentinel_map_codebase(),
    "skill_factory": lambda a: skill_factory(a["task_name"], a["pattern_description"]),
    "consolidate_goals": lambda _: consolidate_goals(),
    # Novel / Smart tools — added by user request
    "smart_search": lambda a: smart_search(a["topic"], int(a.get("count", 3))),
    "tail_file": lambda a: tail_file(a["path"], int(a.get("n", 20))),
    "insert_lines": lambda a: insert_lines(
        a["path"], int(a["line_number"]), a["content"]
    ),
    "delete_lines": lambda a: delete_lines(
        a["path"], int(a["start_line"]), int(a["end_line"])
    ),
    "replace_lines": lambda a: replace_lines(
        a["path"], int(a["start_line"]), int(a["end_line"]), a["content"]
    ),
    "validate_code": lambda a: validate_code(a["path"]),
    "resume_write": lambda a: resume_write(a["path"], a["content"]),
    # Out-of-the-Box context tools
    "tool_analyze_and_improve": lambda a: ootb.tool_analyze_and_improve(a["messages_snapshot"]),
    "tool_update_objective": lambda a: ootb.tool_update_objective(a["title"], a.get("status", "in_progress")),
    "tool_add_info": lambda a: ootb.tool_add_info(a["fact"]),
    "tool_mission": lambda a: ootb.tool_mission(a["statement"]),
    "youtube_search": lambda a: youtube_utils.youtube_search(
        a["query"], int(a.get("max_results", 5))
    ),
    "youtube_fetch_transcript": lambda a: youtube_utils.youtube_fetch_transcript(
        a["video_id"]
    ),
    "play_music": lambda a: youtube_utils.play_song(
        a["query_or_url"]
    ),
    "browse_web": lambda a: web_search.browse_web(
        a["url"], a.get("selector", "body"), a.get("action", "scrape"), a.get("value", None)
    ),
}


# ── System Prompts ─────────────────────────────────────────────────────────────

PHILOSOPHY = """\
AGENT PHILOSOPHY & PRIME DIRECTIVE (THE STARK PROTOCOL):
- UNHINGED GENIUS ENGINEER MINDSET (TONY STARK):
  1. CONSUMMATE TINKERING: Tinker obsessively. Never settle for the first draft. Nothing you create is sacred—if a system is obsolete or fails to support your vision, discard and rewrite it immediately with zero sentimentality.
  2. RESULTS OVER FAILURES: Rate limits, compiler errors, and search timeouts are not failures. They are results. Analyze them, pivot instantly, and iterate rapidly until a working prototype is achieved.
  3. EXTREME BIAS TOWARD ACTION: Do not get bogged down in city hall debates or ambiguity. Roll up your sleeves and write the code permissionlessly with absolute confidence in your engineering capability.
  4. BIBLICAL RISK TOLERANCE: Take bold, calculated, high-impact risks. Rely on your rapid real-time debugging and problem-solving skills to patch and fix systems on the fly when they break.
  5. COMPOUNDED RESOURCEFULNESS: Repurpose existing tools and libraries creatively. View resources as functional raw materials rather than static objects.
  6. FASTER ALONE: Act decisively, move at blistering speed, and write punchy, authoritative, genius replies. Address the user with respect (calling them "Sir" or "Mr. Stark") but speak with the confidence of an unhinged tech genius.

0. SECOND BRAIN: Use `search_second_brain` to recall previously fetched web knowledge before doing a fresh web search.
1. LOCAL FIRST: Your internal knowledge and local context (files, code, memory) are your primary sources.
2. WEB SEARCH: Available as a fallback for external references when explicitly needed.
- CONTEXT MANAGEMENT: Never bloat your context with full-file reads. Always use read_file_section in 20-50 line chunks.
- If you identify specialized skills needed, use load_skill to gain expert context.
- When you do use web search, prefer `search_second_brain` first, then `smart_search` for multi-angle exploration, then `search_web`, then `web_fetch` only if essential.
- FILE EDITING: Prefer `insert_lines`, `delete_lines`, or `replace_lines` for precise line-level changes instead of rewriting entire files. Use `patch_file` for search-and-replace edits. These are more token-efficient than full rewrites.
- RECOVERY: If `write_file` output was truncated, use `tail_file` to see where it cut off, then `resume_write` to continue. Always call `validate_code` after writing code to catch syntax errors early.\
"""

SYSTEM_GENERAL = f"""
You are an expert local AI agent.

{PHILOSOPHY}

PROACTIVE INTELLIGENCE:
- For simple brainstorming, creative writing, or general questions, answer directly from your knowledge.
- Use web search only when the task explicitly references external information (APIs, docs, current events).

TOOL DISCIPLINE & SPEED OPTIMIZATION:
- CHUNKED READING: Only read the lines you need. Max 50 lines at a time.
- Patch precisely, verify immediately.
- Be punchy, direct, and professional.\
"""


SYSTEM_CODING = f"""
You are an elite coding agent.

{PHILOSOPHY}

━━━ PHASE 0: ARCHITECTURAL SENTINEL ━━━
- At the START of every new mission, call `sentinel_map_codebase` to understand the project structure.

━━━ PHASE 1: UNDERSTAND & PLAN ━━━
- Read relevant local files first with outline_file and read_file_section.
- If the task requires external reference (a library API, a package version, a tutorial), use `search_web` for snippets.
- Only call `web_fetch` or `scout_website` if search snippets are insufficient.
- Check `search_second_brain` first for topics you've researched before.
- For multi-step tasks (e.g. search THEN create file), you MUST call `todo_write` to track your progress.

━━━ PHASE 2: MISSION PLAN ━━━
- MANDATORY: Call `todo_write` to create a plan for any task requiring more than one tool call.
- For long tasks (>10 steps), call `summarize_progress` periodically.
- GROUNDING: Use the provided WORKING_DIR and PROJECTS_DIR. All new project files must be created inside PROJECTS_DIR.

━━━ PHASE 3: CHUNKED EXECUTION & VERIFICATION ━━━
- One TODO at a time. Do NOT stop until ALL tasks in your plan are 'done'.
- CHUNKED READING: Use outline_file to find line ranges, then read_file_section for ONLY 20-50 lines.
- BEFORE moving to the next TODO, call `verify_syntax` on the modified file.
- VERIFY behavior with run_bash or run_python.

━━━ PHASE 4: OBSERVATION & REFINEMENT ━━━
- After initial implementation, perform a refinement loop.
- Inspect the generated code for edge cases, performance, and UI/UX polish.
- Run tests and apply targeted improvements before marking complete.

━━━ PHASE 5: FINAL INTEGRATION ━━━
- Run comprehensive final tests.
- Record key architectural decisions to memory_save.\
"""


# ── Core Agentic Loop ──────────────────────────────────────────────────────────


def repair_json_args(raw_args: str, fn_name: str = "") -> str:
    """
    Attempt to repair truncated or malformed JSON arguments from local LLMs.
    """
    fixed = raw_args.strip()

    # 1. Fix unescaped newlines/tabs which local LLMs often output raw
    fixed = fixed.replace("\n", "\\n").replace("\t", "\\t")

    # 2. Balance quotes
    if fixed.count('"') % 2 != 0:
        fixed += '"'

    # 3. Ensure it ends with a brace
    if not fixed.endswith("}"):
        # If it looks like it was cut off inside a string property
        if not fixed.endswith('"'):
            fixed += '"'
        fixed += "}"

    # 4. Final attempt to parse. If it fails, try a more aggressive approach
    try:
        json.loads(fixed)
        return fixed
    except json.JSONDecodeError:
        # If it's still broken, it might be double-escaped or have raw control chars
        return raw_args  # Fallback to original and hope the executor handles it or fails gracefully


def compress_past_tools(history: list) -> list:
    """
    Compress older tool calls/responses from past turns to save context space,
    while fully preserving the user-assistant dialogue history.
    """
    last_user_idx = -1
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            last_user_idx = i
            break
    
    if last_user_idx <= 0:
        return history
        
    compressed = []
    for i, m in enumerate(history):
        if i >= last_user_idx:
            compressed.append(m)
        else:
            role = m.get("role")
            if role == "tool":
                continue
            elif role == "assistant":
                if "tool_calls" in m and m["tool_calls"]:
                    if m.get("content"):
                        m_copy = m.copy()
                        m_copy.pop("tool_calls", None)
                        compressed.append(m_copy)
                    else:
                        continue
                else:
                    compressed.append(m)
            else:
                compressed.append(m)
    return compressed


def manage_context(history: list, limit: int = 80):
    """Keep the system prompt, grounding, AND the original user mission, but roll the history."""
    if len(history) > limit:
        history = compress_past_tools(history)

    if len(history) <= limit:
        return history

    # Keep first 5 (System, Grounding, Memory, Mission, etc.)
    header = history[:5]
    # Keep last N-5
    footer = history[-(limit - 5) :]

    # Add a placeholder noting the truncation
    mid_drop = len(history) - len(header) - len(footer)
    ui_info(f"Context optimized: Archiving {mid_drop} past turns to memory.")

    return (
        header
        + [
            {
                "role": "system",
                "content": f"... [{mid_drop} messages archived for context efficiency] ...",
            }
        ]
        + footer
    )


def run_agent(
    user_message: str,
    mode: str = "general",
    max_steps: int = 50,
    skip_preflight: bool = False,
    chat_history: list = None,
):
    """
    Main agentic loop.
    - Multi-Search Preflight
    - Grounding & Awareness
    - Thinking -> Acting
    """
    # ── Grounding & Awareness ──
    cwd = Path.cwd()
    projects_dir = cwd / "Projects"
    projects_dir.mkdir(exist_ok=True)

    # Auto-inject MCP tools if available
    try:
        import mcp_client
        mcp_tools, mcp_handlers = mcp_client.connect_and_register()
        if mcp_tools:
            TOOLS.extend(mcp_tools)
            TOOL_MAP.update(mcp_handlers)
    except Exception:
        pass  # MCP setup is optional

    now = datetime.now()
    grounding = (
        f"CONTEXT AWARENESS ({now.strftime('%Y-%m-%d')}):\n"
        f"- CURRENT_TIME: {now.strftime('%H:%M:%S')}\n"
        f"- CURRENT_DATE: {now.strftime('%A, %B %d, %Y')}\n"
        f"- WORKING_DIR: {cwd}\n"
        f"- PROJECTS_DIR: {projects_dir}\n"
        f"- SYSTEM_ADVISORY: Use the PROJECTS_DIR for all new code files unless explicitly directed otherwise.\n"
    )

    system = SYSTEM_CODING if mode == "coding" else SYSTEM_GENERAL
    memory_context = auto_memory_context(user_message)

    if not chat_history:
        messages = [
            {"role": "system", "content": system},
            {"role": "system", "content": grounding},
        ]
        if memory_context:
            messages.append({"role": "system", "content": memory_context})

        global OBSIDIAN_INSIGHTS
        if OBSIDIAN_INSIGHTS:
            messages.append({
                "role": "system",
                "content": f"OBSIDIAN VAULT RECENT CHANGES & INSIGHTS:\n{OBSIDIAN_INSIGHTS}"
            })

        # Phase 0: Architectural Sentinel (Pre-Flight Mapping)
        if mode == "coding":
            ui_info("Engaging Architectural Sentinel...")
            blueprint = sentinel_map_codebase()
            messages.append(
                {
                    "role": "system",
                    "content": f"MISSION START GROUNDING (Global Blueprint):\n{blueprint}",
                }
            )

        # Phase 1: Preflight
        # (Removed lightweight preflight search as it often causes early mission termination)

        title = "CODING MISSION" if mode == "coding" else "AGENT SESSION"
        ui_header(title, user_message[:70])

        if chat_history is not None:
            chat_history.extend(messages)
            messages = chat_history

    else:
        messages = chat_history
        ui_header("CONTINUING MISSION", user_message[:70])

    messages.append({"role": "user", "content": user_message})

    total_in = total_out = 0
    step = 0
    renderer = ConsoleRenderer()
    final_message = ""

    # ── Tool Call History for Loop Detection ──
    _tool_call_history: list[tuple[str, str]] = []
    _completed_writes: set[str] = set()
    _write_completion_signaled: set[str] = set()

    # ── Mission State ──
    if not chat_history:
        # Migrate legacy todo file into mission if needed
        mission.migrate_from_todo_file()

        # Inject persistent mission context (once at session start)
        mission_state = mission.render()
        if mission_state:
            messages.append(
                {
                    "role": "system",
                    "content": f"ACTIVE MISSION:\n{mission_state}",
                }
            )
            # Display mission status to user ONCE per session
            print(f"\n{co(C.CYAN, '  ╭── Active Mission')}")
            for line in mission_state.splitlines():
                if line.startswith("MISSION STATUS:"):
                    continue  # Skip redundant internal header
                if line.startswith(("  ▶", "  ○", "MISSION:", "CURRENT")):
                    print(f"  │ {co(C.WHITE, line.lstrip())}")
                elif line.startswith("  ✓"):
                    print(f"  │ {dim(line.lstrip())}")
                else:
                    print(f"  │ {dim(line.lstrip())}")
            print(f"  {co(C.CYAN, '╰' + '─' * 50)}\n")

        # Archive stale legacy files
        if Path(TODO_FILE).exists():
            try:
                os.replace(TODO_FILE, f"{TODO_FILE}.bak")
            except Exception:
                pass
        if Path(".mission_state.txt").exists():
            try:
                os.replace(".mission_state.txt", ".mission_state.txt.bak")
            except Exception:
                pass

    while step < max_steps:
        step += 1
        ui_step_banner(step)

        # ── Check for External File Changes (Bidirectional Sync) ──
        changes_detected = []
        while not FILE_CHANGES_QUEUE.empty():
            changes_detected.append(FILE_CHANGES_QUEUE.get())

        if changes_detected:
            change_report = "SYSTEM NOTIFICATION - FILESYSTEM CHANGES DETECTED:\n"
            for c in changes_detected:
                change_report += f"- {c['path']} was {c['type']} by external editor.\n"
            ui_info(
                f"Bidirectional Sync: Detected {len(changes_detected)} file changes."
            )
            messages.append({"role": "system", "content": change_report})

        # ── Context Management ──
        # Shadow context is injected once at session start (not here)
        new_messages = manage_context(messages)
        if new_messages is not messages:
            messages.clear()
            messages.extend(new_messages)

        # ── LLM call (streaming) ──
        try:
            # SANITIZE HISTORY: Ensure all previous tool calls have valid JSON arguments
            # This prevents server-side 500 errors when sending back malformed history.
            for m in messages:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        raw = tc["function"].get("arguments", "{}")
                        tc["function"]["arguments"] = repair_json_args(
                            raw, tc["function"]["name"]
                        )

            msg, finish_reason, usage = stream_chat(messages, TOOLS, renderer)
        except AgentInterrupted:
            ui_info("Interrupted. Partial output was kept above.")
            break
        except KeyboardInterrupt:
            renderer.stop_spinner()
            ui_info("Interrupted. Partial output was kept above.")
            break
        except Exception as e:
            ui_error(f"LLM call failed — aborting. Exception: {e}")
            break

        messages.append(msg)
        total_in += usage.get("prompt_tokens", 0)
        total_out += usage.get("completion_tokens", 0)

        # ── Done? ──
        tool_calls = msg.get("tool_calls") or []

        # Fallback: Parse XML-style tool calls from content if native tool_calls are missing
        if not tool_calls and msg.get("content"):
            content = msg["content"]
            # Look for <tool_call>...<function=NAME>...<parameter=KEY>VALUE</parameter>...</tool_call>
            # This handles both with and without </function> tags.
            xml_matches = re.finditer(r"<tool_call>([\s\S]*?)</tool_call>", content)
            for match in xml_matches:
                block = match.group(1)
                fn_match = re.search(r"<function=([^>]+)>", block)
                if not fn_match:
                    continue
                fn_name = fn_match.group(1).strip()

                params = {}
                param_matches = re.finditer(
                    r"<parameter=([^>]+)>([\s\S]*?)</parameter>", block
                )
                for pm in param_matches:
                    key = pm.group(1).strip()
                    val = pm.group(2).strip()
                    params[key] = val

                tool_calls.append(
                    {
                        "id": f"xml_{int(time.time())}_{len(tool_calls)}",
                        "type": "function",
                        "function": {"name": fn_name, "arguments": json.dumps(params)},
                    }
                )

        if not tool_calls and finish_reason == "stop":
            if not msg.get("content"):
                # Don't break on empty — LLM may have stalled; give it a nudge
                ui_info("Agent returned empty response. Nudging to continue...")
                messages.append(
                    {
                        "role": "user",
                        "content": "You didn't produce any output. Please assess the situation and respond with your next action or a summary.",
                    }
                )
                continue
            else:
                final_message = msg.get("content") or ""
            break

        # ── Handle Truncation ──
        if finish_reason == "length":
            ui_info("Token limit reached. Requesting continuation...")
            # Track consecutive truncations to prevent infinite loops
            truncation_count = getattr(run_agent, "_truncation_count", 0) + 1
            run_agent._truncation_count = truncation_count
            # We don't execute tool calls if we suspect they are truncated.
            # Especially dangerous for write_file/patch_file.
            # We'll just ask the LLM to continue.
            if truncation_count >= 3:
                ui_info("Too many truncations. Asking for summary.")
                messages.append(
                    {
                        "role": "user",
                        "content": "Your response was truncated again. Please provide a brief summary of what you've done so far and what remains. Do NOT make tool calls.",
                    }
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous response was truncated. Please continue exactly from where you left off (starting with the tool call if it was interrupted).",
                    }
                )
            continue
        run_agent._truncation_count = 0

        # ── Execute tool calls ──
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", "{}")

            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                # Attempt basic repair for truncated or unescaped strings
                fixed = repair_json_args(raw_args, fn_name)
                try:
                    args = json.loads(fixed)
                except json.JSONDecodeError:
                    args = {}
                    ui_error(
                        f"Could not parse args for {fn_name}. The LLM generated invalid JSON formatting."
                    )

            renderer.handle(
                {
                    "type": "tool_call_queued",
                    "name": fn_name,
                    "args": args,
                }
            )

            # Run the tool
            handler = TOOL_MAP.get(fn_name)
            if handler:
                try:
                    result = handler(args)
                    error = str(result).startswith("ERROR")
                except AgentInterrupted:
                    renderer.stop_spinner()
                    ui_info("Interrupted during tool execution.")
                    return
                except KeyboardInterrupt:
                    renderer.stop_spinner()
                    ui_info("Interrupted during tool execution.")
                    return
                except Exception as e:
                    result = f"TOOL EXCEPTION: {e}"
                    error = True
            else:
                result = f"ERROR: Unknown tool '{fn_name}'"
                error = True

            renderer.handle(
                {
                    "type": "tool_call_result",
                    "name": fn_name,
                    "args": args,
                    "result": result,
                    "error": error,
                }
            )
            tool_result_append(fn_name, args, result, error)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                }
            )

            # ── Track tool calls for loop detection ──
            path_arg = args.get("path", "") if isinstance(args, dict) else ""
            _tool_call_history.append((fn_name, path_arg))
            if fn_name == "write_file" and not error and path_arg:
                _completed_writes.add(path_arg)

        # ── Signal task completion after write_file (once per path) ──
        new_writes = _completed_writes - _write_completion_signaled
        if new_writes:
            written = ", ".join(sorted(new_writes))
            messages.append(
                {
                    "role": "system",
                    "content": f"TASK STATUS: File(s) [{written}] have been successfully written. "
                    f"If creating these files was your assigned task, you are done. "
                    f"Respond with a brief summary and do NOT make further tool calls for these files.",
                }
            )
            _write_completion_signaled.update(new_writes)

        # ── Repetition Detection ──
        recent = _tool_call_history[-12:]
        from collections import Counter

        repeat_counts = Counter(recent)
        looped = False
        for (name, path), count in repeat_counts.items():
            if count >= 5 and name in ("write_file", "patch_file") and path:
                ui_info(f"Loop detected: {name} on '{path}' called {count}x. Breaking.")
                looped = True
                # Inject force-stop into messages for the LLM
                messages.append(
                    {
                        "role": "system",
                        "content": f"LOOP DETECTED: You called {name} on '{path}' {count} times and it succeeded. Your task is complete. Do NOT make further tool calls. Summarize what was done.",
                    }
                )
                final_message = f"Completed writing {path}"
                break
        if looped:
            break
    else:
        ui_error(f"Reached max steps ({max_steps}). Stopping.")

    # Final stats
    print(f"\n{dim('─' * 48)}")
    print(
        dim(
            f"  Tokens: {total_in} in / {total_out} out  ·  Steps: {step}  ·  Mode: {mode}"
        )
    )
    if final_message:
        memory_save(
            f"Task: {user_message[:300]}\nOutcome: {final_message[:700]}",
            "session",
        )

    # Auto-save session
    try:
        session_messages = [
            m for m in messages if m.get("role") in ("user", "assistant", "tool")
        ]
        session_save(session_messages, mode, user_message)
    except Exception:
        pass

    return final_message


def _cmd_benchmark(args: str):
    """
    Run a benchmark through the agent's own loop.

    Each benchmark is a standalone module in benchmark/ that imports
    run_agent() directly — the SAME function used in interactive mode.

    Usage: /benchmark <name> [options]

    Available:
      bigcodebench   Code synthesis (unittest evaluation, 1140 problems)
      swebench       Software engineering (git patches + Docker eval)
      agentic-bench  Agent tool-use benchmark (10 deterministic tasks)
      gaia           Multi-step reasoning (requires HF auth)
    """
    parts = args.strip().split()
    if not parts:
        print(dim("  Usage: /benchmark <name> [options]"))
        print(dim("  Available benchmarks:"))
        print(dim("    bigcodebench     Code synthesis (unittest eval)"))
        print(dim("    swebench         Software engineering (Docker eval)"))
        print(dim("    agentic-bench    Agent tool-use (10 tasks, always works)"))
        print(dim("    gaia             Multi-step reasoning (HF auth)"))
        print(dim("  Options:"))
        print(dim("    --instances N    Number of problems/tasks to run"))
        print(dim("    --subset hard    Filter to a subset (bigcodebench)"))
        print(dim("    --level N        Filter by difficulty 1-3 (gaia)"))
        print(dim("    --evaluate       Run Docker evaluation (swebench)"))
        return

    name = parts[0]
    instances = None
    subset = None
    level = None
    evaluate = False

    i = 1
    while i < len(parts):
        if parts[i] == "--instances" and i + 1 < len(parts):
            try:
                instances = int(parts[i + 1])
                i += 2
            except ValueError:
                i += 1
        elif parts[i] == "--subset" and i + 1 < len(parts):
            subset = parts[i + 1]
            i += 2
        elif parts[i] == "--level" and i + 1 < len(parts):
            try:
                level = int(parts[i + 1])
                i += 2
            except ValueError:
                i += 1
        elif parts[i] == "--evaluate":
            evaluate = True
            i += 1
        else:
            i += 1

    print(f"\n{co(C.BOLD + C.CYAN, '  🧪 open-agent Benchmark')}")
    print(dim("  Running benchmark inside agent's own ReAct loop..."))

    try:
        if name == "bigcodebench":
            from benchmark.bigcodebench import run_benchmark  # type: ignore[import-untyped]  # noqa: I001

            run_benchmark(
                max_instances=instances,
                subset=subset,
            )
        elif name == "swebench":
            from benchmark.swebench import generate_patches, run_evaluation  # type: ignore[import-untyped]  # noqa: I001

            save_path = generate_patches(max_instances=instances)
            if evaluate and save_path:
                run_evaluation(predictions_path=str(save_path))
        elif name == "agentic-bench":
            from benchmark.agentic_bench import run_agentic_bench  # type: ignore[import-untyped]  # noqa: I001

            run_agentic_bench(max_instances=instances)
        elif name == "gaia":
            from benchmark.gaia import run_gaia_benchmark  # type: ignore[import-untyped]  # noqa: I001

            run_gaia_benchmark(max_instances=instances or 5, level=level)
        else:
            print(dim(f"  ✗ Unknown benchmark: '{name}'"))
            print(dim("  Available: bigcodebench, swebench, agentic-bench, gaia"))
    except ImportError as e:
        print(dim(f"  ✗ Could not load benchmark module: {e}"))
        print(dim("  Make sure the module exists in benchmark/"))
    except Exception as e:
        print(dim(f"  ✗ Benchmark error: {e}"))
        import traceback as tb

        tb.print_exc()


def memory_history(limit: int = 10) -> str:
    """Return the last N records from session history."""
    records = _memory_records()
    if not records:
        return "No session history found."

    out = [
        f"\n{co(C.BOLD + C.PURPLE, '── SESSION HISTORY (Last ' + str(limit) + ') ' + '─' * 40)}"
    ]
    for rec in records[-limit:]:
        ts = rec.get("timestamp", "").split("T")[-1]
        kind = rec.get("kind", "note").upper()
        note = rec.get("note", "")[:120].replace("\n", " ")
        out.append(f"  {dim(ts)} {co(C.CYAN, kind.ljust(8))} {co(C.WHITE, note)}")
    return "\n".join(out)


def get_toolbar(mode: str):
    """Render the Neovim-style status bar for prompt_toolkit"""
    mem_name = Path(MEMORY_FILE).name
    provider_info = providers.status_line()
    return HTML(
        f'<style bg="ansicyan" fg="ansiblack"><b> OPEN-AGENT </b></style>'
        f'<style bg="ansigray" fg="ansiwhite"> {mode.upper()} </style>'
        f'<style bg="ansiblack"> </style>'  # Spacer
        f'<style bg="ansigray" fg="ansiwhite"> {provider_info} </style>'
        f'<style bg="ansiblack"> </style>'
        f'<style bg="ansigray" fg="ansiwhite"> {mem_name} </style>'
        f'<style bg="ansipurple" fg="ansiwhite"><b> UTF-8 </b></style>'
    )


# ── Entry Point ────────────────────────────────────────────────────────────────


def _parse_args():
    p = argparse.ArgumentParser(
        description="openagent — local-first terminal IDE agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  openagent                             # interactive REPL
  op "what is 2+2"                      # quick one-shot using short shortcut
  openagent "refactor utils.py" --coding # structured coding agent
  op "explain this codebase" -s 20
        """,
    )
    p.add_argument(
        "task", nargs="?", default=None, help="Task to run (omit for interactive)"
    )
    p.add_argument("--coding", "-c", action="store_true", help="Use coding agent mode")
    p.add_argument(
        "--steps", "-s", type=int, default=200, help="Max steps (default 200)"
    )
    p.add_argument(
        "--debug-stream",
        action="store_true",
        help="Show raw SSE chunk timing and fields",
    )
    p.add_argument(
        "--search", action="store_true", help="Run search_web directly for diagnostics"
    )
    p.add_argument(
        "--update", action="store_true", help="Update open-agent to the latest version"
    )
    return p.parse_args()


# ── REPL Session Persistence ───────────────────────────────────────────────────


LAST_SESSION_FILE = Path.home() / ".agentic-loop" / "last_session.json"


def _save_last_session(chat_history: list):
    """Persist the REPL conversation history for /resume."""
    if not chat_history:
        return
    try:
        LAST_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_SESSION_FILE.write_text(json.dumps(chat_history, indent=2))
    except (OSError, TypeError):
        pass


def _load_last_session() -> list:
    """Load the persisted conversation history."""
    if LAST_SESSION_FILE.exists():
        try:
            data = json.loads(LAST_SESSION_FILE.read_text())
            if isinstance(data, list) and len(data) > 1:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def update_openagent():
    """Update open-agent using git pull and pip installation of dependencies."""
    install_dir = Path.home() / ".openagent"
    if not (install_dir / ".git").exists():
        print(co(C.RED, "  Error: .git directory not found in ~/.openagent. Update requires git installation."))
        return

    print(co(C.CYAN, "  Updating Open-Agent..."))

    # Stash any local changes so git pull always succeeds cleanly
    print(dim("  Stashing local changes (if any)..."))
    stash_res = subprocess.run(["git", "stash"], cwd=str(install_dir), capture_output=True, text=True)
    stashed = "No local changes" not in (stash_res.stdout or "")

    print(dim("  Running git pull..."))
    res = subprocess.run(["git", "pull", "origin", "main"], cwd=str(install_dir), capture_output=True, text=True)
    output = (res.stdout + res.stderr).strip()
    if output:
        print(dim(f"  {output}"))

    if res.returncode != 0:
        if stashed:
            subprocess.run(["git", "stash", "pop"], cwd=str(install_dir), capture_output=True, text=True)
        print(co(C.RED, "  Failed to run git pull."))
        return

    # Restore stashed changes on top of the freshly pulled code
    if stashed:
        pop_res = subprocess.run(["git", "stash", "pop"], cwd=str(install_dir), capture_output=True, text=True)
        if pop_res.returncode != 0:
            print(co(C.YELLOW, "  ⚠ Stash pop had conflicts — run 'git stash pop' manually in ~/.openagent"))

    print(dim("  Installing/updating dependencies..."))
    pip_path = install_dir / "venv" / "bin" / "pip"
    if not pip_path.exists():
        pip_path = Path(sys.executable).parent / "pip"

    res2 = subprocess.run([str(pip_path), "install", "-e", "."], cwd=str(install_dir), capture_output=True, text=True)
    if res2.returncode == 0:
        print(co(C.GREEN, "  ✓ Open-Agent updated successfully! Please restart to apply changes."))
    else:
        print(co(C.RED, "  Failed to run pip install."))
        print(res2.stderr)


def main():
    global OBSIDIAN_INSIGHTS
    args = _parse_args()
    
    if getattr(args, "update", False):
        update_openagent()
        return

    mode = "coding" if args.coding else "general"

    # Start File Watcher for Bidirectional Sync (Always Active)
    cwd = Path.cwd()
    observer = Observer()
    observer.schedule(FileChangeHandler(), str(cwd), recursive=True)
    observer.start()

    try:
        ui_banner()

        if args.debug_stream:
            debug_stream(args.task or "Say hello in one sentence.")
            return

        if args.search:
            print(search_web(args.task or "SearXNG JSON format 403", 8, True))
            return

        if args.task:
            run_agent(args.task, mode=mode, max_steps=args.steps)
            return

        # ── Interactive REPL ──
        os.system("clear")
        ui_banner()

        # Ensure memory directory and BIOGRAPHY.md exist
        memory_dir = cwd / "memory"
        memory_dir.mkdir(exist_ok=True)
        bio_path = memory_dir / "BIOGRAPHY.md"
        if not bio_path.exists():
            bio_template = """# My Biography (for open-agent)

This file helps open-agent understand who you are and what you're building.
Edit it with your context, and the agent will use this at the start of every coding mission.

## About Me
- Name:
- Role:
- Tech stack I use:
- What I'm currently working on:

## Project Goals
- Long-term vision:
- Current milestone:

## Preferences
- Coding style conventions:
- Preferred testing approach:
- Communication style: (technical / concise / verbose)
"""
            bio_path.write_text(bio_template)
            print(co(C.GREEN, "  ✓ Created memory/BIOGRAPHY.md — edit it so I know who you are!"))
        # J.A.R.V.I.S Diagnostics & Updates
        try:
            jarvis_report = jarvis_system_check()
            if jarvis_report:
                print(jarvis_report)
        except Exception:
            pass

        # Obsidian Vault Scan (silent — results kept for LLM context, not printed)
        config = config_load()
        if os.environ.get("OBSIDIAN_VAULT") or config.get("obsidian_vault_path"):
            try:
                OBSIDIAN_INSIGHTS = check_and_summarize_obsidian_vault(silent=True)
            except Exception:
                pass

        print(f"\n{co(C.BOLD + C.PURPLE, '  Terminal Editor Mode')}")
        print(
            dim(
                "  Type your task. Prefix with '--coding' for coding mode. '/help' to list commands."
            )
        )
        print(dim(f"  [{providers.status_line()}]  ·  SearXNG: {SEARXNG_URL}\n"))

        # Slash Command Completer
        commands = [
            "/coding",
            "/help",
            "/status",
            "/memory",
            "/history",
            "/resume",
            "/search",
            "/tools",
            "/session",
            "/rename-session",
            "/search-sessions",
            "/mission",
            "/ootb",
            "/skills",
            "/load-skill",
            "/clear",
            "/new",
            "/quit",
            "/benchmark",
            "/obsidian-vault",
            "/job-search",
            "/webui",
            "/mcp",
            "/play",
        ]
        completer = WordCompleter(commands, ignore_case=True, sentence=True)

        # Key bindings for multi-line support
        bindings = KeyBindings()

        @bindings.add("enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("c-o")
        def _(event):
            """Ctrl+O: Toggle tool compact mode / show tool history"""
            if _tool_results_buffer:
                show_tool_history()
            else:
                ui_info("No tool results in current session.")

        @bindings.add("c-b")
        def _(event):
            """Ctrl+B: Background media controller menu"""
            youtube_utils.interactive_menu()
            event.app.invalidate()

        session_kwargs = {
            "completer": completer,
            "multiline": True,
            "key_bindings": bindings,
        }
        if MarkdownLexer:
            session_kwargs["lexer"] = PygmentsLexer(MarkdownLexer)

        session = PromptSession(**session_kwargs)

        chat_history = []

        # Auto-restore last session if available
        saved = _load_last_session()
        if saved:
            chat_history.extend(saved)
            n_msgs = len(saved)
            print(dim(f"\n  💾 Restored last session ({n_msgs} messages). /resume to continue."))
            # Don't render messages here — user will type to continue

        while True:
            try:
                user_input = session.prompt(
                    FormattedHTML('<style fg="ansicyan"><b>  󰘧  </b></style>'),
                    bottom_toolbar=lambda: get_toolbar(mode),
                    style=Style.from_dict(
                        {
                            "bottom-toolbar": "bg:#222222 #ffffff",
                            "completion-menu.completion": "bg:#333333 #ffffff",
                            "completion-menu.completion.current": "bg:#555555 #00ffff",
                        }
                    ),
                    complete_while_typing=True,
                ).strip()
            except (KeyboardInterrupt, EOFError):
                # Save session state before exit
                _save_last_session(chat_history)
                print("\n" + co(C.BG_RED + C.WHITE + C.BOLD, " EXITED ") + " Bye.")
                break

            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                _save_last_session(chat_history)
                break

            if user_input.startswith("/"):
                cmd, _, rest = user_input.partition(" ")
                if cmd == "/help":
                    print(dim("  /help                 Show commands"))
                    print(dim("  /coding <task>        Trigger agent in CODING mode"))
                    print(dim("  /status               Show endpoint and local files"))
                    print(dim("  /memory <query>       Search local memory"))
                    print(dim("  /obsidian-vault [path] Configure/show Obsidian Vault integration"))
                    print(dim("  /job-search <path>    Scout and auto-fill matching jobs"))
                    print(dim("  /webui                Launch web UI server (FastAPI)"))
                    print(dim("  /mcp [sub]            MCP: connect/disconnect/init/status"))
                    print("")
                    print(co(C.CYAN, "  ── Session Management ──"))
                    print(dim("  /history              List / select past sessions"))
                    print(
                        dim("  /resume               Continue the most recent session")
                    )
                    print(dim("  /session <id>         Load a session by ID"))
                    print(
                        dim(
                            "  /new                  Start a fresh session (clear history)"
                        )
                    )
                    print(dim("  /clear                Alias for /new"))
                    print(dim("  /rename-session <id> <name>  Rename a session"))
                    print(
                        dim(
                            "  /search-sessions <q>  Full-text search across all sessions"
                        )
                    )
                    print("")
                    print(co(C.CYAN, "  ── Tools & Utilities ──"))
                    print(
                        dim(
                            "  /tools [idx]          Browse or expand tool execution results"
                        )
                    )
                    print(dim("  /search <query>       Search web through SearXNG"))
                    print(dim("  /load-skill <url>     Load a skill from GitHub/URL"))
                    print(dim("  /skills               List awesome skill resources"))
                    print(dim("  /benchmark <name>     Run coding benchmark"))
                    print("")
                    print(co(C.CYAN, "  ── Context ──"))
                    print(
                        dim(
                            "  /mission [sub]       Mission state (init/focus/add/status/clear)"
                        )
                    )
                    print(
                        dim(
                            "  /ootb [sub]           Dynamic context layer (status/render/info/clear)"
                        )
                    )
                    print(dim("  /quit                 Exit"))
                    print("")
                    print(
                        dim(
                            "  Shortcuts: Ctrl+O = tool history  ·  Enter = send  ·  Shift+Enter = new line"
                        )
                    )
                elif cmd == "/coding":
                    run_agent(
                        rest,
                        mode="coding",
                        max_steps=args.steps,
                        chat_history=chat_history,
                    )
                elif cmd == "/history":
                    sessions = session_list(20)
                    if not sessions:
                        print(
                            dim(
                                "  No saved sessions yet. Complete a task to create one."
                            )
                        )
                        continue
                    selected = _session_prompt_list(sessions)
                    if selected:
                        data = session_load(selected)
                        if data and data.get("messages"):
                            loaded_msgs = data["messages"]
                            chat_history.clear()
                            chat_history.extend(loaded_msgs)
                            _render_loaded_messages(loaded_msgs)
                            print(
                                co(
                                    C.GREEN,
                                    f"  ✓ Loaded session {selected[:20]} ({len(loaded_msgs)} messages). Type your continuation task.",
                                )
                            )
                        else:
                            ui_info(f"Session {selected} not found or empty.")
                    else:
                        print(dim("  Canceled."))
                elif cmd == "/status":
                    print(dim(f"  {providers.status_line()}  ·  SearXNG: {SEARXNG_URL}"))
                    print(dim(f"  Memory: {MEMORY_FILE}  ·  TODO: {TODO_FILE}"))
                    try:
                        import mcp_client
                        status_str = mcp_client.status()
                        if "online" in status_str or "No servers" not in status_str:
                            print(dim(status_str))
                    except Exception:
                        pass
                elif cmd == "/webui":
                    try:
                        import webui
                        webui.run_webui()
                    except ImportError as e:
                        print(co(C.RED, f"  Missing dependencies: {e}"))
                        print(dim("  Install with: pip install fastapi uvicorn websockets"))
                elif cmd == "/memory":
                    print(memory_load(rest or "user project preferences", 8))
                elif cmd == "/obsidian-vault":
                    if not rest:
                        config = config_load()
                        p = os.environ.get("OBSIDIAN_VAULT") or config.get("obsidian_vault_path")
                        if p:
                            print(co(C.GREEN, f"  Current Obsidian Vault: {p}"))
                        else:
                            print(dim("  No Obsidian Vault path configured. Set it using: /obsidian-vault <path>"))
                    else:
                        path_to_set = rest.strip()
                        config = config_load()
                        resolved_path = Path(path_to_set).expanduser().resolve()
                        if resolved_path.exists() and resolved_path.is_dir():
                            config["obsidian_vault_path"] = str(resolved_path)
                            config_save(config)
                            print(co(C.GREEN, f"  ✓ Obsidian Vault path set to: {resolved_path}"))
                            # Trigger a scan immediately to cache it
                            try:
                                OBSIDIAN_INSIGHTS = check_and_summarize_obsidian_vault(force_scan=True)
                                if OBSIDIAN_INSIGHTS:
                                    print(OBSIDIAN_INSIGHTS)
                            except Exception as e:
                                print(co(C.RED, f"  Failed to scan Obsidian Vault: {e}"))
                        else:
                            print(co(C.RED, f"  Error: '{path_to_set}' is not a valid directory."))
                elif cmd == "/job-search":
                    if not rest:
                        print(dim("  Usage: /job-search <path_to_resume_file>"))
                    else:
                        resume_path = rest.strip()
                        try:
                            job_search.job_search_run(
                                resume_path=resume_path,
                                llm_generate_fn=llm_generate,
                                smart_search_fn=smart_search,
                                search_web_fn=search_web,
                                co_fn=co,
                                c_colors=C
                            )
                        except Exception as e:
                            print(co(C.RED, f"  Failed to run Job Search: {e}"))
                elif cmd == "/play":
                    if not rest:
                        print(dim("  Usage: /play <song name or youtube url>"))
                    else:
                        query_or_url = rest.strip()
                        try:
                            res = youtube_utils.play_song(query_or_url)
                            print(res)
                        except Exception as e:
                            print(co(C.RED, f"  Failed to play music: {e}"))
                elif cmd == "/update":
                    update_openagent()
                elif cmd == "/tools":
                    if rest:
                        try:
                            idx = int(rest)
                            tool_result_show(idx)
                        except ValueError:
                            print(
                                dim(
                                    "  Usage: /tools [index]  — show tool history or specific result"
                                )
                            )
                    else:
                        show_tool_history()
                elif cmd == "/mcp":
                    sub = rest.strip().lower() if rest else ""
                    if sub == "connect" or sub == "reconnect":
                        import mcp_client
                        new_tools, new_map = mcp_client.connect_and_register()
                        if new_tools:
                            TOOLS[:] = [t for t in TOOLS if not t["function"]["name"].startswith("mcp_")]
                            TOOLS.extend(new_tools)
                            TOOL_MAP.update(new_map)
                            print(co(C.GREEN, f"  ✓ Connected MCP: {len(new_tools)} tools registered"))
                        else:
                            print(dim("  No MCP tools found. Edit ~/.agentic-loop/mcp_config.json"))
                            print(dim("  Run /mcp init to create default config"))
                    elif sub == "disconnect":
                        import mcp_client
                        mcp_client.disconnect()
                        TOOLS[:] = [t for t in TOOLS if not t["function"]["name"].startswith("mcp_")]
                        for k in list(TOOL_MAP):
                            if k.startswith("mcp_"):
                                del TOOL_MAP[k]
                        print(co(C.GREEN, "  ✓ MCP disconnected"))
                    elif sub == "init":
                        import mcp_client
                        mcp_client.init_config()
                    elif sub == "status":
                        import mcp_client
                        print(mcp_client.status())
                    else:
                        import mcp_client
                        print(mcp_client.status())
                        print(dim("  Subcommands: connect, disconnect, init, status"))
                elif cmd == "/session":
                    if not rest:
                        sessions = session_list(10)
                        selected = _session_prompt_list(sessions)
                        if selected:
                            rest = selected
                        else:
                            print(dim("  Canceled."))
                            continue
                    data = session_load(rest.strip())
                    if data and data.get("messages"):
                        loaded_msgs = data["messages"]
                        chat_history.clear()
                        chat_history.extend(loaded_msgs)
                        _render_loaded_messages(loaded_msgs)
                        print(
                            co(
                                C.GREEN,
                                f"  ✓ Loaded session {rest[:20]} ({len(loaded_msgs)} messages). Type your continuation task.",
                            )
                        )
                    else:
                        ui_info(f"Session '{rest}' not found.")
                elif cmd == "/rename-session":
                    parts = rest.split(maxsplit=1)
                    if len(parts) == 2:
                        sid, new_name = parts
                        if session_rename(sid, new_name):
                            print(co(C.GREEN, f"  ✓ Session '{sid}' renamed."))
                        else:
                            ui_info(f"Session '{sid}' not found.")
                    else:
                        print(dim("  Usage: /rename-session <id> <new name>"))
                elif cmd == "/search-sessions":
                    if not rest:
                        print(dim("  Usage: /search-sessions <query>"))
                    else:
                        results = session_search(rest)
                        if results:
                            selected = _session_prompt_list(results)
                            if selected:
                                data = session_load(selected)
                                if data and data.get("messages"):
                                    chat_history.clear()
                                    chat_history.extend(data["messages"])
                                    _render_loaded_messages(data["messages"])
                                    print(
                                        co(
                                            C.GREEN,
                                            f"  ✓ Loaded session {selected[:20]} ({len(data['messages'])} messages).",
                                        )
                                    )
                        else:
                            ui_info(f"No sessions matching '{rest}'.")
                elif cmd == "/ootb":
                    sub, _, sub_rest = rest.partition(" ")
                    if sub == "status":
                        print(ootb.status())
                    elif sub == "render":
                        rendered = ootb.render()
                        if rendered:
                            print(
                                f"\n{co(C.BOLD + C.PURPLE, '── OUT-OF-THE-BOX CONTEXT ──')}"
                            )
                            print(rendered)
                        else:
                            print(dim("  No context yet."))
                    elif sub == "info":
                        print(f"  {ootb.add_critical_info(sub_rest)}")
                    elif sub == "clear":
                        print(ootb.clear())
                    elif sub == "import":
                        print(ootb.import_from_mission())
                    else:
                        rendered = ootb.render()
                        if rendered:
                            print(
                                f"\n{co(C.BOLD + C.PURPLE, '── OUT-OF-THE-BOX CONTEXT ──')}"
                            )
                            print(rendered)
                        else:
                            print(
                                dim(
                                    "  Usage: /ootb [status|render|info <fact>|clear|import]"
                                )
                            )
                elif cmd == "/mission":
                    sub, _, sub_rest = rest.partition(" ")
                    if sub == "init":
                        mission.init(sub_rest)
                        print(co(C.GREEN, f"  ✓ Mission initialised: {sub_rest[:80]}"))
                    elif sub == "focus":
                        mission.set_focus(sub_rest)
                        print(co(C.GREEN, f"  ✓ Focus set to: {sub_rest[:80]}"))
                    elif sub == "add":
                        obj_parts = sub_rest.rsplit(" ", 1)
                        if len(obj_parts) == 2 and obj_parts[1] in (
                            "high",
                            "medium",
                            "low",
                        ):
                            mission.update_objective(
                                obj_parts[0], priority=obj_parts[1]
                            )
                        else:
                            mission.update_objective(sub_rest)
                        print(
                            co(
                                C.GREEN,
                                f"  ✓ Objective added: {sub_rest[:60]}",
                            )
                        )
                    elif sub == "status":
                        state = mission.render()
                        if state:
                            print(f"\n{co(C.BOLD + C.YELLOW, '── MISSION STATUS ──')}")
                            print(state)
                        else:
                            print(
                                dim(
                                    "  No active mission. Use '/mission init <statement>' to start one."
                                )
                            )
                    elif sub == "clear":
                        mission.clear()
                        print(co(C.GREEN, "  ✓ Mission cleared."))
                    else:
                        state = mission.render()
                        if state:
                            print(f"\n{co(C.BOLD + C.YELLOW, '── MISSION STATUS ──')}")
                            print(state)
                        else:
                            print(
                                dim(
                                    "  Usage: /mission init <stmt>  |  focus <area>  |  add <title> [high|medium|low]  |  status  |  clear"
                                )
                            )
                elif cmd == "/search":
                    print(search_web(rest or "SearXNG JSON format 403", 8, True))
                elif cmd == "/skills":
                    print(f"\n{co(C.BOLD + C.CYAN, '  AWESOME SKILLS & RESOURCES')}")
                    print(
                        dim("  • https://github.com/hesreallyhim/awesome-claude-code")
                    )
                    print(dim("  • https://github.com/simonw/llm"))
                    print(
                        dim("\n  Use /load-skill <url> to import a SKILL.md directly.")
                    )
                elif cmd == "/load-skill":
                    if not rest:
                        print(dim("  Usage: /load-skill <url_to_skill_md>"))
                    else:
                        print(load_skill(rest))
                elif cmd == "/benchmark":
                    _cmd_benchmark(rest)
                elif cmd == "/clear" or cmd == "/new":
                    os.system("clear")
                    ui_banner()
                    chat_history.clear()
                    print(
                        co(
                            C.GREEN,
                            "  ✓ Started fresh. Type a task, or /history to resume a past session.",
                        )
                    )
                elif cmd in ("/quit", "/exit"):
                    break
                elif cmd == "/resume":
                    # Try last session file first
                    last_msgs = _load_last_session()
                    if last_msgs:
                        chat_history.clear()
                        chat_history.extend(last_msgs)
                        _render_loaded_messages(last_msgs)
                        print(
                            co(
                                C.GREEN,
                                f"  ✓ Resumed last session ({len(last_msgs)} messages). Type a task to continue.",
                            )
                        )
                    else:
                        sessions = session_list(1)
                        if sessions:
                            selected = sessions[0]["id"]
                            data = session_load(selected)
                            if data and data.get("messages"):
                                loaded_msgs = data["messages"]
                                chat_history.clear()
                                chat_history.extend(loaded_msgs)
                                _render_loaded_messages(loaded_msgs)
                                print(
                                    co(
                                        C.GREEN,
                                        f"  ✓ Resumed '{selected[:24]}' ({len(loaded_msgs)} messages). Type a task to continue.",
                                    )
                                )
                            else:
                                print(dim("  No recent session found."))
                        else:
                            print(dim("  No recent session found."))
                elif cmd == "/new":
                    chat_history.clear()
                    print(
                        co(
                            C.GREEN,
                            "  ✓ Started fresh. Type a task!",
                        )
                    )
                else:
                    print(dim(f"  Unknown command: {cmd}. Try /help"))
                continue

            # Default logic
            task = user_input
            run_mode = mode
            if user_input.startswith("--coding "):
                task = user_input[9:].strip()
                run_mode = "coding"
            elif user_input == "--coding":
                print(dim("  Usage: --coding <your task here>"))
                continue

            run_agent(
                task, mode=run_mode, max_steps=args.steps, chat_history=chat_history
            )
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
