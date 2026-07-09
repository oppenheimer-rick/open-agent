import os
import sys
import json
import subprocess
import argparse
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer

try:
    from pygments.lexers.markup import MarkdownLexer
except ImportError:
    MarkdownLexer = None

from ui.console import (
    C, co, dim, trunc, ui_banner, ui_info, ui_header, ui_error
)
from core.config import config_load, config_save
from core.session import session_list, session_load, session_rename, session_search
from core.agent import run_agent, llm_generate, smart_search
from tools.builtin import search_web, load_skill, verify_syntax
from tools.registry import TOOLS, TOOL_MAP, show_tool_history, tool_result_show
from core.state import FILE_CHANGES_QUEUE, ACTIVE_MESSAGES
import providers
import mission
import out_of_the_box as ootb
import youtube_utils
import job_search

LAST_SESSION_FILE = Path.home() / ".agentic-loop" / "last_session.json"
TODO_FILE = ".agent_todo.json"
MEMORY_FILE = ".agent_memory.jsonl"
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8081/search")

class FileChangeHandler(FileSystemEventHandler):
    def _is_agent_change(self, path: str) -> bool:
        from core.state import AGENT_MODIFIED_FILES
        if path in AGENT_MODIFIED_FILES:
            if time.time() - AGENT_MODIFIED_FILES[path] < 2.0:
                return True
        return False

    def on_any_event(self, event):
        if event.is_directory:
            return
        
        src_path = event.src_path
        p = Path(src_path)
        
        # Performance: Pre-filter directories to prevent slow-down and loop sync noise
        ignored_parts = {".git", "venv", "node_modules", "dynamic_tools", "__pycache__", 
                         ".ruff_cache", ".pytest_cache", ".pi-lens", "dist", "build"}
        if any(part in p.parts for part in ignored_parts) or p.name.startswith("."):
            return
            
        if event.event_type in ("modified", "created"):
            if p.suffix in (".py", ".js", ".html", ".css", ".md", ".txt", ".json"):
                abs_path = str(p.absolute())
                if not self._is_agent_change(abs_path):
                    FILE_CHANGES_QUEUE.put({"path": p.name, "type": event.event_type})

def _format_sessions_table(sessions: list) -> str:
    """Render session list as a formatted table with numbered entries."""
    if not sessions:
        return dim("  No sessions found.")
    lines = [f"\n{co(C.BOLD + C.PURPLE, '── SESSION HISTORY ──')}"]
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

def run_local_oauth_flow(client_id, client_secret):
    """Run a local OAuth2 authorization flow, spinning up a temporary HTTP server on port 8080."""
    import http.server
    import socketserver
    import webbrowser
    import urllib.parse
    import threading
    import httpx
    import secrets

    redirect_uri = "http://localhost:8080"
    auth_code = None
    server_instance = None
    # CSRF mitigation: Generate cryptographically secure state parameter
    oauth_state = secrets.token_hex(16)
    
    class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            parsed_url = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            
            # CSRF mitigation check
            received_state = query_params.get("state", [None])[0]
            if received_state != oauth_state:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                html = """
                <html>
                <body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #121212; color: #ff3333;">
                    <div style="text-align: center; border: 1px solid #ff3333; padding: 40px; border-radius: 10px; background-color: #1e1e1e; box-shadow: 0 4px 6px rgba(0,0,0,0.3);">
                        <h1>✗ Security Error</h1>
                        <p style="color: #aaaaaa;">CSRF validation failed: State parameter mismatch. Linking aborted.</p>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(html.encode("utf-8"))
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            
            if "code" in query_params:
                auth_code = query_params["code"][0]
                html = """
                <html>
                <body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #121212; color: #ffffff;">
                    <div style="text-align: center; border: 1px solid #333; padding: 40px; border-radius: 10px; background-color: #1e1e1e; box-shadow: 0 4px 6px rgba(0,0,0,0.3);">
                        <h1 style="color: #4caf50; margin-bottom: 20px;">✓ Authentication Successful</h1>
                        <p style="color: #aaaaaa;">Google Account successfully linked to open-agent.</p>
                        <p style="color: #666666; font-size: 14px; margin-top: 20px;">You can close this tab and return to the terminal.</p>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(html.encode("utf-8"))
                threading.Thread(target=server_instance.shutdown).start()
            else:
                html = "<html><body><h1>Error connecting account. Close this window and try again.</h1></body></html>"
                self.wfile.write(html.encode("utf-8"))

        def log_message(self, format, *args):
            pass

    # Ensure TCPServer binds only to 127.0.0.1 (localhost)
    socketserver.TCPServer.allow_reuse_address = True
    try:
        server_instance = socketserver.TCPServer(("127.0.0.1", 8080), OAuthCallbackHandler)
    except Exception as e:
        print(f"\n  Error: Could not start local server on port 8080 ({e}).")
        return None

    scopes = "https://mail.google.com/ https://www.googleapis.com/auth/userinfo.email"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": oauth_state,
            "access_type": "offline",
            "prompt": "consent"
        })
    )
    
    print(f"\n  🔗 Opening browser for Google Sign-in...")
    print(f"  If the browser doesn't open automatically, click here:\n  {auth_url}\n")
    
    # Thread leak mitigation: 5-minute auto-timeout
    def auto_timeout_checker():
        time.sleep(300) # 5 minutes
        if not auth_code:
            print(co(C.RED, "\n  ⚠ OAuth login session timed out (5 mins). Shutting down local server..."))
            try:
                server_instance.shutdown()
            except Exception:
                pass

    timeout_thread = threading.Thread(target=auto_timeout_checker, daemon=True)
    timeout_thread.start()

    server_thread = threading.Thread(target=server_instance.serve_forever)
    server_thread.start()
    
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
        
    server_thread.join()
    server_instance.server_close()
    
    if not auth_code:
        print("  Error: Authorization code not received or session timed out.")
        return None
        
    print("  Exchanging authorization code for tokens...")
    try:
        r = httpx.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        })
        if r.status_code == 200:
            tokens = r.json()
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in", 3600)
            
            ru = httpx.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={
                "Authorization": f"Bearer {access_token}"
            })
            if ru.status_code == 200:
                userinfo = ru.json()
                email_address = userinfo.get("email")
                if email_address:
                    return {
                        "imap_host": "imap.gmail.com",
                        "imap_port": 993,
                        "imap_user": email_address,
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "token_expires_at": time.time() + expires_in,
                        "smtp_host": "smtp.gmail.com",
                        "smtp_port": 587,
                        "auth_type": "oauth2"
                    }
            else:
                print(f"  Error fetching userinfo: {ru.text}")
        else:
            print(f"  Error exchanging code: {r.text}")
    except Exception as e:
        print(f"  Error during token exchange: {e}")
        
    return None

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

    for s in sessions:
        if s["id"] == choice or s["id"].endswith(choice):
            return s["id"]

    if choice.startswith("search ") or choice.startswith("s "):
        q = choice[choice.index(" ") + 1 :]
        results = session_search(q)
        if results:
            return _session_prompt_list(results)
        ui_info(f"No sessions matching '{q}'.")
        return None

    ui_info(f"Session '{choice}' not found.")
    return None

def _cmd_benchmark(args: str):
    """Run a benchmark through the agent's own loop."""
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
            from benchmark.bigcodebench import run_benchmark
            run_benchmark(max_instances=instances, subset=subset)
        elif name == "swebench":
            from benchmark.swebench import generate_patches, run_evaluation
            save_path = generate_patches(max_instances=instances)
            if evaluate and save_path:
                run_evaluation(predictions_path=str(save_path))
        elif name == "agentic-bench":
            from benchmark.agentic_bench import run_agentic_bench
            run_agentic_bench(max_instances=instances)
        elif name == "gaia":
            from benchmark.gaia import run_gaia_benchmark
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
    from memory import records as _memory_records
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
    """Render status bar."""
    mem_name = Path(MEMORY_FILE).name
    provider_info = providers.status_line()
    is_offline = os.environ.get("OPENAGENT_OFFLINE") == "1"
    net_status = "OFFLINE" if is_offline else "ONLINE"
    net_color = "red" if is_offline else "green"
    return HTML(
        f'<style bg="ansicyan" fg="ansiblack"><b> OPEN-AGENT </b></style>'
        f'<style bg="ansigray" fg="ansiwhite"> {mode.upper()} </style>'
        f'<style bg="ansiblack"> </style>'
        f'<style bg="ansi{net_color}" fg="ansiwhite"><b> {net_status} </b></style>'
        f'<style bg="ansiblack"> </style>'
        f'<style bg="ansigray" fg="ansiwhite"> {provider_info} </style>'
        f'<style bg="ansiblack"> </style>'
        f'<style bg="ansigray" fg="ansiwhite"> {mem_name} </style>'
        f'<style bg="ansipurple" fg="ansiwhite"><b> UTF-8 </b></style>'
    )

def _save_last_session(chat_history: list):
    """Persist conversation history."""
    if not chat_history:
        return
    try:
        LAST_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_SESSION_FILE.write_text(json.dumps(chat_history, indent=2), encoding="utf-8")
    except (OSError, TypeError):
        pass

def _load_last_session() -> list:
    """Load persisted conversation history."""
    if LAST_SESSION_FILE.exists():
        try:
            data = json.loads(LAST_SESSION_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 1:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []

def update_openagent():
    install_dir = Path.home() / ".openagent"
    if not (install_dir / ".git").exists():
        print(co(C.RED, "  Error: .git directory not found in ~/.openagent. Update requires git."))
        return

    print(co(C.CYAN, "  Updating Open-Agent..."))
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

def repl_loop(mode_default: str = "general", max_steps_default: int = 200, debug_stream_flag: bool = False, run_search_flag: bool = False, run_task_flag: str = None):
    global ACTIVE_MESSAGES
    mode = mode_default
    cwd = Path.cwd()
    observer = Observer()
    # Watch root directory non-recursively to avoid scanning venv/ or .git/ at startup
    observer.schedule(FileChangeHandler(), str(cwd), recursive=False)
    # Recursively watch Projects/ if it exists, which contains user code and is small
    projects_path = cwd / "Projects"
    if projects_path.exists():
        observer.schedule(FileChangeHandler(), str(projects_path), recursive=True)
    observer.start()

    try:
        if debug_stream_flag:
            from core.agent import debug_stream
            debug_stream(run_task_flag or "Say hello in one sentence.")
            return

        if run_search_flag:
            print(search_web(run_task_flag or "SearXNG JSON format 403", 8))
            return

        if run_task_flag:
            run_agent(run_task_flag, mode=mode, max_steps=max_steps_default)
            return

        # Start Interactive REPL
        os.system("clear")
        ui_banner()

        # Ensure memory dir & bio
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
            bio_path.write_text(bio_template, encoding="utf-8")
            print(co(C.GREEN, "  ✓ Created memory/BIOGRAPHY.md — edit it so I know who you are!"))

        # Scan Obsidian Vault
        config = config_load()
        from ui.diagnostics import check_and_summarize_obsidian_vault, jarvis_system_check
        obsidian_insights = ""
        if os.environ.get("OBSIDIAN_VAULT") or config.get("obsidian_vault_path"):
            try:
                obsidian_insights = check_and_summarize_obsidian_vault(silent=True)
            except Exception:
                pass

        # Print J.A.R.V.I.S. diagnostics dashboard on boot
        try:
            print(jarvis_system_check())
        except Exception as e:
            print(co(C.RED, f"  Failed to run J.A.R.V.I.S. diagnostics: {e}"))

        is_offline = os.environ.get("OPENAGENT_OFFLINE") == "1"
        status_banner = co(C.BG_RED + C.WHITE + C.BOLD, " OFFLINE ") if is_offline else co(C.BG_PURPLE + C.WHITE + C.BOLD, " ONLINE ")
        print(f"\n{co(C.BOLD + C.PURPLE, '  Terminal Editor Mode')}  ·  {status_banner}")
        print(dim("  Type your task. Prefix with '--coding' for coding mode. '/help' to list commands."))
        print(dim(f"  [{providers.status_line()}]  ·  SearXNG: {SEARXNG_URL if not is_offline else 'offline'}\n"))

        commands = [
            "/coding", "/help", "/status", "/memory", "/history", "/resume",
            "/search", "/tools", "/session", "/rename-session", "/search-sessions",
            "/mission", "/ootb", "/skills", "/load-skill", "/clear", "/new",
            "/quit", "/benchmark", "/obsidian-vault", "/job-search", "/webui",
            "/mcp", "/play", "/mail",
        ]
        completer = WordCompleter(commands, ignore_case=True, sentence=True)
        bindings = KeyBindings()

        @bindings.add("enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("c-o")
        def _(event):
            import core.state
            from tools.registry import tool_result_show, _tool_results_buffer
            current = getattr(core.state, "EXPAND_TOOL_OUTPUT", False)
            new_state = not current
            core.state.EXPAND_TOOL_OUTPUT = new_state
            
            if new_state:
                print("\n" + co(C.GREEN, "  ℹ Output expansion enabled (full outputs will be shown)"))
                if _tool_results_buffer:
                    print(tool_result_show(-1))
                else:
                    print(dim("  No tool execution history to show yet."))
            else:
                print("\n" + co(C.YELLOW, "  ℹ Output expansion disabled (outputs will be truncated)"))
            event.app.invalidate()

        @bindings.add("c-b")
        def _(event):
            youtube_utils.interactive_menu()
            event.app.invalidate()

        session_kwargs = {
            "completer": completer,
            "multiline": True,
            "key_bindings": bindings,
        }
        # Disabled PygmentsLexer on user prompt to prevent terminal typing freezes/input lag
        pass

        prompt_session = PromptSession(**session_kwargs)
        chat_history = []

        # Don't auto-restore session — it bloats the small 61k context window.
        # Use /resume explicitly when you want to continue a previous conversation.
        # saved = _load_last_session()
        # if saved:
        #     chat_history.extend(saved)
        #     print(dim(f"\n  💾 Restored last session ({len(saved)} messages). /resume to continue."))
        print(dim("\n  💡 Session auto-restore disabled. Use /resume to continue previous work."))

        last_ctrl_c_time = 0
        while True:
            try:
                user_input = prompt_session.prompt(
                    HTML('<style fg="ansicyan"><b>  󰘧  </b></style>'),
                    bottom_toolbar=lambda: get_toolbar(mode),
                    style=Style.from_dict({
                        "bottom-toolbar": "bg:#222222 #ffffff",
                        "completion-menu.completion": "bg:#333333 #ffffff",
                        "completion-menu.completion.current": "bg:#555555 #00ffff",
                    }),
                    complete_while_typing=True,
                ).strip()
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - last_ctrl_c_time < 1.5:
                    _save_last_session(chat_history)
                    print("\n" + co(C.BG_RED + C.WHITE + C.BOLD, " EXITED ") + " Bye.")
                    break
                last_ctrl_c_time = now
                print(co(C.YELLOW, "\n  [Ctrl+C] Cancelled. Press Ctrl+C again or type 'exit' to quit."))
                continue
            except EOFError:
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
                    print(dim("  /mail                 Manage and read your local/remote email"))
                    print("")
                    print(co(C.CYAN, "  ── Session Management ──"))
                    print(dim("  /history              List / select past sessions"))
                    print(dim("  /resume               Continue the most recent session"))
                    print(dim("  /session <id>         Load a session by ID"))
                    print(dim("  /new                  Start a fresh session (clear history)"))
                    print(dim("  /clear                Alias for /new"))
                    print(dim("  /rename-session <id> <name>  Rename a session"))
                    print(dim("  /search-sessions <q>  Full-text search across all sessions"))
                    print("")
                    print(co(C.CYAN, "  ── Tools & Utilities ──"))
                    print(dim("  /tools [idx]          Browse or expand tool execution results"))
                    print(dim("  /search <query>       Search web through SearXNG"))
                    print(dim("  /load-skill <url>     Load a skill from GitHub/URL"))
                    print(dim("  /skills               List awesome skill resources"))
                    print(dim("  /benchmark <name>     Run coding benchmark"))
                    print("")
                    print(co(C.CYAN, "  ── Context ──"))
                    print(dim("  /mission [sub]       Mission state (init/focus/add/status/clear)"))
                    print(dim("  /ootb [sub]           Dynamic context layer (status/render/info/clear)"))
                    print(dim("  /quit                 Exit"))
                elif cmd == "/coding":
                    run_agent(rest, mode="coding", max_steps=max_steps_default, chat_history=chat_history)
                elif cmd == "/history":
                    sessions = session_list(20)
                    if not sessions:
                        print(dim("  No saved sessions yet."))
                        continue
                    selected = _session_prompt_list(sessions)
                    if selected:
                        data = session_load(selected)
                        if data and data.get("messages"):
                            loaded_msgs = data["messages"]
                            chat_history.clear()
                            chat_history.extend(loaded_msgs)
                            _render_loaded_messages(loaded_msgs)
                            print(co(C.GREEN, f"  ✓ Loaded session {selected[:20]} ({len(loaded_msgs)} messages)."))
                elif cmd == "/status":
                    print(dim(f"  {providers.status_line()}  ·  SearXNG: {SEARXNG_URL}"))
                    print(dim(f"  Memory: {MEMORY_FILE}  ·  TODO: {TODO_FILE}"))
                    try:
                        import mcp_client
                        print(dim(mcp_client.status()))
                    except Exception:
                        pass
                elif cmd == "/webui":
                    try:
                        import webui
                        webui.run_webui()
                    except ImportError as e:
                        print(co(C.RED, f"  Missing dependencies: {e}"))
                elif cmd == "/memory":
                    from memory import load as memory_load
                    print(memory_load(rest or "user project preferences", 8))
                elif cmd == "/obsidian-vault":
                    if not rest:
                        config = config_load()
                        p = os.environ.get("OBSIDIAN_VAULT") or config.get("obsidian_vault_path")
                        if p:
                            print(co(C.GREEN, f"  Current Obsidian Vault: {p}"))
                        else:
                            print(dim("  No Obsidian Vault configured. Set using: /obsidian-vault <path>"))
                    else:
                        path_to_set = rest.strip()
                        config = config_load()
                        resolved_path = Path(path_to_set).expanduser().resolve()
                        if resolved_path.exists() and resolved_path.is_dir():
                            config["obsidian_vault_path"] = str(resolved_path)
                            config_save(config)
                            print(co(C.GREEN, f"  ✓ Obsidian Vault path set to: {resolved_path}"))
                            try:
                                obsidian_insights = check_and_summarize_obsidian_vault(force_scan=True)
                                if obsidian_insights:
                                    print(obsidian_insights)
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
                            print(youtube_utils.play_song(query_or_url))
                        except Exception as e:
                            print(co(C.RED, f"  Failed to play music: {e}"))
                elif cmd == "/update":
                    update_openagent()
                elif cmd == "/tools":
                    if rest:
                        try:
                            idx = int(rest)
                            print(tool_result_show(idx))
                        except ValueError:
                            print(dim("  Usage: /tools [index]"))
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
                    else:
                        import mcp_client
                        print(mcp_client.status())
                elif cmd == "/mail":
                    config = config_load()
                    accounts = config.get("mail_accounts", [])
                    old_user = config.get("mail_imap_user")
                    old_password = config.get("mail_imap_password")
                    if old_user and old_password and not any(a.get("imap_user") == old_user for a in accounts):
                        accounts.append({
                            "imap_host": config.get("mail_imap_host", "imap.gmail.com"),
                            "imap_port": int(config.get("mail_imap_port", 993)),
                            "imap_user": old_user,
                            "imap_password": old_password,
                            "smtp_host": config.get("mail_smtp_host", "smtp.gmail.com"),
                            "smtp_port": int(config.get("mail_smtp_port", 587)),
                        })
                        config["mail_accounts"] = accounts
                        config_save(config)

                    run_wizard = not accounts
                    if run_wizard:
                        print(co(C.CYAN, "\n  ✉ No email accounts configured yet."))
                        print(co(C.CYAN, "  Choose setup method:"))
                        print(dim("    [1] Sign in with Google (OAuth2 - Recommended for Gmail)"))
                        print(dim("    [2] App Password (IMAP/SMTP - Proton, iCloud, Yahoo, custom)"))
                        print(dim("    [c] Cancel\n"))
                        setup_opt = input("  Select setup option: ").strip().lower()
                        if setup_opt == "c" or not setup_opt:
                            continue
                        elif setup_opt == "1":
                            client_id = config.get("google_client_id")
                            client_secret = config.get("google_client_secret")
                            if not client_id or not client_secret:
                                print(co(C.YELLOW, "\n  ✉ Google OAuth2 configuration is missing."))
                                print(co(C.CYAN, "  To enable \"Sign in with Google\", you need a free Client ID & Secret from Google Cloud Console."))
                                print(co(C.CYAN, "  Setup is fast (1-2 mins):"))
                                print(dim("    1. Go to Google Cloud Console: https://console.developers.google.com/"))
                                print(dim("    2. Create a new project, then search & enable the 'Gmail API'."))
                                print(dim("    3. Configure 'OAuth consent screen' as 'External' and add your Gmail to 'Test Users'."))
                                print(dim("    4. Under 'Credentials', click 'Create Credentials' -> 'OAuth client ID'."))
                                print(dim("    5. Choose 'Desktop app' as Application type and click Create."))
                                print(dim("    6. Copy the Client ID and Client Secret.\n"))
                                client_id = input("  Google Client ID: ").strip()
                                if not client_id:
                                    print(co(C.RED, "  Skipped configuration."))
                                    continue
                                client_secret = input("  Google Client Secret: ").strip()
                                if not client_secret:
                                    print(co(C.RED, "  Skipped configuration."))
                                    continue
                                config["google_client_id"] = client_id
                                config["google_client_secret"] = client_secret
                                config_save(config)
                                print(co(C.GREEN, "  ✓ Google OAuth2 credentials saved successfully!\n"))
                            
                            new_acc = run_local_oauth_flow(client_id, client_secret)
                            if new_acc:
                                accounts.append(new_acc)
                                config["mail_accounts"] = accounts
                                config_save(config)
                                print(co(C.GREEN, f"  ✓ Account {new_acc.get('imap_user')} saved successfully!"))
                                print(co(C.CYAN, "  Connecting to inbox to verify..."))
                                try:
                                    from dynamic_tools.read_emails import read_emails
                                    result = read_emails(limit=5, email_address=new_acc.get('imap_user'))
                                    print(result)
                                except Exception as e:
                                    print(co(C.RED, f"  Failed to run verification: {e}"))
                        elif setup_opt == "2":
                            import getpass
                            print(co(C.YELLOW, "\n  ✉ Configuring new email account (IMAP/SMTP password)."))
                            print(co(C.CYAN, "  (Press Enter to accept defaults, or leave blank to skip/cancel)\n"))
                            imap_host = input("  IMAP Host (default: imap.gmail.com): ").strip() or "imap.gmail.com"
                            imap_user = input("  Email Address: ").strip()
                            if not imap_user:
                                print(co(C.RED, "  Skipped configuration. Email address is required."))
                                continue
                            imap_password = getpass.getpass("  App Password (hidden/app-specific): ").strip()
                            if not imap_password:
                                print(co(C.RED, "  Skipped configuration. Password is required."))
                                continue
                            try:
                                imap_port_val = input("  IMAP Port (default: 993): ").strip()
                                imap_port = int(imap_port_val) if imap_port_val else 993
                            except ValueError:
                                imap_port = 993
                            smtp_host = input("  SMTP Host (default: smtp.gmail.com): ").strip() or "smtp.gmail.com"
                            try:
                                smtp_port_val = input("  SMTP Port (default: 587): ").strip()
                                smtp_port = int(smtp_port_val) if smtp_port_val else 587
                            except ValueError:
                                smtp_port = 587
                            
                            new_acc = {
                                "imap_host": imap_host,
                                "imap_port": imap_port,
                                "imap_user": imap_user,
                                "imap_password": imap_password,
                                "smtp_host": smtp_host,
                                "smtp_port": smtp_port,
                                "auth_type": "password"
                            }
                            accounts.append(new_acc)
                            config["mail_accounts"] = accounts
                            config_save(config)
                            print(co(C.GREEN, f"  ✓ Account {imap_user} saved successfully!"))
                            print(co(C.CYAN, "  Connecting to inbox to verify..."))
                            try:
                                from dynamic_tools.read_emails import read_emails
                                result = read_emails(limit=5, email_address=imap_user)
                                print(result)
                                if "Application-specific password required" in result:
                                    print(co(C.YELLOW, "\n  💡 How to Fix Google Auth Failure:"))
                                    print(dim("    1. Go to your Google Account Security Settings: https://myaccount.google.com/security"))
                                    print(dim("    2. Ensure '2-Step Verification' is turned ON."))
                                    print(dim("    3. Navigate to: https://myaccount.google.com/apppasswords"))
                                    print(dim("    4. Generate a new App Password (e.g. name it 'open-agent')."))
                                    print(dim("    5. Copy the 16-character code (without spaces)."))
                                    print(dim("    6. Run `/mail` again, remove the failed account, and re-add it with the App Password."))
                            except Exception as e:
                                print(co(C.RED, f"  Failed to run verification: {e}"))
                    else:
                        print(co(C.CYAN, f"\n  ✉ Configured Email Accounts ({len(accounts)}):"))
                        for idx, acc in enumerate(accounts, 1):
                            auth_desc = "OAuth2" if acc.get("auth_type") == "oauth2" else "Password"
                            print(dim(f"    [{idx}] {acc.get('imap_user')} ({auth_desc} - {acc.get('imap_host')})"))
                        print("")
                        print(co(C.CYAN, "  Options: "))
                        print(dim("    [g] Add Gmail account via Sign in with Google (OAuth2)"))
                        print(dim("    [p] Add other account via App Password (IMAP/SMTP)"))
                        print(dim("    [r] Remove an account"))
                        print(dim("    [v] Verify / Read all configured accounts"))
                        print(dim("    [1-N] Verify / Read specific account"))
                        print(dim("    [c] Cancel\n"))
                        
                        opt = input("  Choose an option: ").strip().lower()
                        if opt == "c" or not opt:
                            continue
                        elif opt == "g":
                            client_id = config.get("google_client_id")
                            client_secret = config.get("google_client_secret")
                            if not client_id or not client_secret:
                                print(co(C.YELLOW, "\n  ✉ Google OAuth2 configuration is missing."))
                                print(co(C.CYAN, "  To enable \"Sign in with Google\", you need a free Client ID & Secret from Google Cloud Console."))
                                print(co(C.CYAN, "  Setup is fast (1-2 mins):"))
                                print(dim("    1. Go to Google Cloud Console: https://console.developers.google.com/"))
                                print(dim("    2. Create a new project, then search & enable the 'Gmail API'."))
                                print(dim("    3. Configure 'OAuth consent screen' as 'External' and add your Gmail to 'Test Users'."))
                                print(dim("    4. Under 'Credentials', click 'Create Credentials' -> 'OAuth client ID'."))
                                print(dim("    5. Choose 'Desktop app' as Application type and click Create."))
                                print(dim("    6. Copy the Client ID and Client Secret.\n"))
                                client_id = input("  Google Client ID: ").strip()
                                if not client_id:
                                    print(co(C.RED, "  Skipped configuration."))
                                    continue
                                client_secret = input("  Google Client Secret: ").strip()
                                if not client_secret:
                                    print(co(C.RED, "  Skipped configuration."))
                                    continue
                                config["google_client_id"] = client_id
                                config["google_client_secret"] = client_secret
                                config_save(config)
                                print(co(C.GREEN, "  ✓ Google OAuth2 credentials saved successfully!\n"))
                            
                            new_acc = run_local_oauth_flow(client_id, client_secret)
                            if new_acc:
                                accounts.append(new_acc)
                                config["mail_accounts"] = accounts
                                config_save(config)
                                print(co(C.GREEN, f"  ✓ Account {new_acc.get('imap_user')} saved successfully!"))
                            continue
                        elif opt == "p":
                            import getpass
                            print(co(C.YELLOW, "\n  ✉ Configuring new email account (IMAP/SMTP password)."))
                            print(co(C.CYAN, "  (Press Enter to accept defaults, or leave blank to skip/cancel)\n"))
                            imap_host = input("  IMAP Host (default: imap.gmail.com): ").strip() or "imap.gmail.com"
                            imap_user = input("  Email Address: ").strip()
                            if not imap_user:
                                print(co(C.RED, "  Skipped configuration. Email address is required."))
                                continue
                            if any(a.get("imap_user").lower() == imap_user.lower() for a in accounts):
                                print(co(C.RED, f"  Account {imap_user} is already configured."))
                                continue
                            imap_password = getpass.getpass("  App Password (hidden/app-specific): ").strip()
                            if not imap_password:
                                print(co(C.RED, "  Skipped configuration. Password is required."))
                                continue
                            try:
                                imap_port_val = input("  IMAP Port (default: 993): ").strip()
                                imap_port = int(imap_port_val) if imap_port_val else 993
                            except ValueError:
                                imap_port = 993
                            smtp_host = input("  SMTP Host (default: smtp.gmail.com): ").strip() or "smtp.gmail.com"
                            try:
                                smtp_port_val = input("  SMTP Port (default: 587): ").strip()
                                smtp_port = int(smtp_port_val) if smtp_port_val else 587
                            except ValueError:
                                smtp_port = 587
                            
                            new_acc = {
                                "imap_host": imap_host,
                                "imap_port": imap_port,
                                "imap_user": imap_user,
                                "imap_password": imap_password,
                                "smtp_host": smtp_host,
                                "smtp_port": smtp_port,
                                "auth_type": "password"
                            }
                            accounts.append(new_acc)
                            config["mail_accounts"] = accounts
                            config_save(config)
                            print(co(C.GREEN, f"  ✓ Account {imap_user} saved successfully!"))
                            continue
                        elif opt == "r":
                            val = input("  Enter account number to remove: ").strip()
                            try:
                                rem_idx = int(val) - 1
                                if 0 <= rem_idx < len(accounts):
                                    removed = accounts.pop(rem_idx)
                                    config["mail_accounts"] = accounts
                                    config_save(config)
                                    print(co(C.GREEN, f"  ✓ Removed account {removed.get('imap_user')}"))
                                else:
                                    print(co(C.RED, "  Invalid index."))
                            except ValueError:
                                print(co(C.RED, "  Invalid index."))
                            continue
                        elif opt == "v":
                            email_filter = None
                        elif opt.isdigit():
                            sel_idx = int(opt) - 1
                            if 0 <= sel_idx < len(accounts):
                                email_filter = accounts[sel_idx].get("imap_user")
                            else:
                                print(co(C.RED, "  Invalid index."))
                                continue
                        else:
                            print(co(C.RED, "  Invalid option."))
                            continue
                            
                        print(co(C.CYAN, "  Connecting to inbox to verify..."))
                        try:
                            from dynamic_tools.read_emails import read_emails
                            result = read_emails(limit=5, email_address=email_filter)
                            print(result)
                            if "Application-specific password required" in result:
                                print(co(C.YELLOW, "\n  💡 How to Fix Google Auth Failure:"))
                                print(dim("    1. Go to your Google Account Security Settings: https://myaccount.google.com/security"))
                                print(dim("    2. Ensure '2-Step Verification' is turned ON."))
                                print(dim("    3. Navigate to: https://myaccount.google.com/apppasswords"))
                                print(dim("    4. Generate a new App Password (e.g. name it 'open-agent')."))
                                print(dim("    5. Copy the 16-character code (without spaces)."))
                                print(dim("    6. Run `/mail` again, remove the failed account, and re-add it with the App Password."))
                        except Exception as e:
                            print(co(C.RED, f"  Failed to run verification: {e}"))
                elif cmd == "/session":
                    if not rest:
                        sessions = session_list(10)
                        selected = _session_prompt_list(sessions)
                        if selected:
                            rest = selected
                        else:
                            continue
                    data = session_load(rest.strip())
                    if data and data.get("messages"):
                        loaded_msgs = data["messages"]
                        chat_history.clear()
                        chat_history.extend(loaded_msgs)
                        _render_loaded_messages(loaded_msgs)
                        print(co(C.GREEN, f"  ✓ Loaded session {rest[:20]} ({len(loaded_msgs)} messages)."))
                elif cmd == "/rename-session":
                    parts = rest.split(maxsplit=1)
                    if len(parts) == 2:
                        sid, new_name = parts
                        if session_rename(sid, new_name):
                            print(co(C.GREEN, f"  ✓ Session renamed."))
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
                elif cmd == "/ootb":
                    sub, _, sub_rest = rest.partition(" ")
                    if sub == "status":
                        print(ootb.status())
                    elif sub == "render":
                        print(ootb.render() or "  No context yet.")
                    elif sub == "info":
                        print(f"  {ootb.add_critical_info(sub_rest)}")
                    elif sub == "clear":
                        print(ootb.clear())
                    elif sub == "import":
                        print(ootb.import_from_mission())
                elif cmd == "/mission":
                    sub, _, sub_rest = rest.partition(" ")
                    if sub == "init":
                        mission.init(sub_rest)
                        print(co(C.GREEN, f"  ✓ Mission initialised."))
                    elif sub == "focus":
                        mission.set_focus(sub_rest)
                        print(co(C.GREEN, f"  ✓ Focus set."))
                    elif sub == "add":
                        obj_parts = sub_rest.rsplit(" ", 1)
                        if len(obj_parts) == 2 and obj_parts[1] in ("high", "medium", "low"):
                            mission.update_objective(obj_parts[0], priority=obj_parts[1])
                        else:
                            mission.update_objective(sub_rest)
                        print(co(C.GREEN, f"  ✓ Objective added."))
                    elif sub == "status":
                        state = mission.render()
                        if state:
                            print(f"\n{co(C.BOLD + C.YELLOW, '── MISSION STATUS ──')}\n{state}")
                    elif sub == "clear":
                        mission.clear()
                        print(co(C.GREEN, "  ✓ Mission cleared."))
                elif cmd == "/search":
                    print(search_web(rest or "SearXNG JSON format 403", 8))
                elif cmd == "/skills":
                    print(f"\n{co(C.BOLD + C.CYAN, '  AWESOME SKILLS & RESOURCES')}")
                    print(dim("  • https://github.com/hesreallyhim/awesome-claude-code"))
                    print(dim("  • https://github.com/simonw/llm"))
                    print(dim("\n  Use /load-skill <url> to import a SKILL.md directly."))
                elif cmd == "/load-skill":
                    print(load_skill(rest))
                elif cmd == "/benchmark":
                    _cmd_benchmark(rest)
                elif cmd == "/clear" or cmd == "/new":
                    os.system("clear")
                    ui_banner()
                    chat_history.clear()
                    print(co(C.GREEN, "  ✓ Started fresh."))
                elif cmd == "/resume":
                    last_msgs = _load_last_session()
                    if last_msgs:
                        chat_history.clear()
                        chat_history.extend(last_msgs)
                        _render_loaded_messages(last_msgs)
                        print(co(C.GREEN, f"  ✓ Resumed last session ({len(last_msgs)} messages)."))
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
                                print(co(C.GREEN, f"  ✓ Resumed '{selected[:24]}' ({len(loaded_msgs)} messages)."))
                else:
                    print(dim(f"  Unknown command: {cmd}"))
                continue

            task = user_input
            run_mode = mode
            if user_input.startswith("--coding "):
                task = user_input[9:].strip()
                run_mode = "coding"
            elif user_input == "--coding":
                print(dim("  Usage: --coding <your task here>"))
                continue

            # Auto-resume last session on "continue"/"c"/"cont" if history is empty
            if task.lower() in ("continue", "c", "cont") and not chat_history:
                last_msgs = _load_last_session()
                if last_msgs:
                    chat_history.extend(last_msgs)
                    _render_loaded_messages(last_msgs)
                    print(co(C.GREEN, f"  ✓ Auto-resumed last session ({len(last_msgs)} messages)."))
                else:
                    sessions = session_list(1)
                    if sessions:
                        selected = sessions[0]["id"]
                        data = session_load(selected)
                        if data and data.get("messages"):
                            loaded_msgs = data["messages"]
                            chat_history.extend(loaded_msgs)
                            _render_loaded_messages(loaded_msgs)
                            print(co(C.GREEN, f"  ✓ Auto-resumed last session '{selected[:24]}' ({len(loaded_msgs)} messages)."))

            ACTIVE_MESSAGES = chat_history
            run_agent(task, mode=run_mode, max_steps=max_steps_default, chat_history=chat_history)
    finally:
        observer.stop()
        observer.join()
