import sys
import re
import shutil
import time
import threading

# ── ANSI / Terminal Colors ───────────────────────────────────────────────────
class C:
    """ANSI color codes — Claude-inspired Palette"""
    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITAL = "\033[3m"
    UL = "\033[4m"
    
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

# ── Logos ────────────────────────────────────────────────────────────────────
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

# ── UI Primitives & Icons ───────────────────────────────────────────────────
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
    print("\n")
    for line in logo.strip("\n").splitlines():
        print(line.center(width))
    print()

def visual_len(text: str) -> int:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return len(ansi_escape.sub('', text))

def pad_line(text: str, width: int = 80, bg: str = "") -> str:
    vlen = visual_len(text)
    padding = max(0, width - vlen)
    return bg + text + " " * padding + "\033[0m"

# ── ConsoleRenderer ─────────────────────────────────────────────────────────
class ConsoleRenderer:
    """
    Append-only terminal renderer with live-streaming and markdown-lite support.
    """
    def __init__(self):
        self.spinner = None
        self.block_type = None
        self.in_bold = False
        self.in_code_block = False
        self._code_lines = []
        self._code_line_buf = ""
        self._code_lang = ""
        self._code_line_count = 0
        self.line_start = True
        self.header_level = 0
        self.usage = {}
        self.step = 0
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
                clean = (
                    delta_args.replace("\\n", "\n  ")
                    .replace("\\t", "    ")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )
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
        if not code.strip():
            return ""
        try:
            from pygments import highlight
            from pygments.lexers import get_lexer_by_name, guess_lexer
            from pygments.formatters import Terminal256Formatter

            if lang:
                try:
                    lexer = get_lexer_by_name(lang, stripall=True)
                except Exception:
                    lexer = guess_lexer(code)
            else:
                lexer = guess_lexer(code)

            code = code.rstrip("\n")
            return highlight(code, lexer, Terminal256Formatter(style="monokai"))
        except ImportError:
            return co(C.GRAY, code)
        except Exception:
            return co(C.GRAY, code)

    def write_delta(self, text: str):
        i = 0
        while i < len(text):
            char = text[i]
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
                sys.stdout.flush()
                continue

            if char == "`" and i + 2 < len(text) and text[i : i + 3] == "```":
                if not self.in_code_block:
                    self.in_code_block = True
                    self._code_lang = ""
                    self._code_lines = []
                    self._code_line_count = 0
                    i += 3
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
                    self.in_code_block = False
                    i += 3
                    
                    code_lines = self._code_lines.copy()
                    lang = self._code_lang
                    
                    KNOWN_LANGS = {
                        "python", "py", "javascript", "js", "typescript", "ts",
                        "bash", "sh", "html", "css", "json", "yaml", "yml",
                        "sql", "c", "cpp", "rust", "rs", "go", "java", "ruby", "rb"
                    }
                    if code_lines:
                        first_val = code_lines[0].strip().lower()
                        if first_val in KNOWN_LANGS:
                            if not lang or lang.lower() == first_val:
                                lang = first_val
                                code_lines.pop(0)
                                
                    highlighted = self._highlight_code(
                        "\n".join(code_lines).rstrip(), lang
                    )
                    
                    term_width = shutil.get_terminal_size().columns
                    width = min(110, max(50, term_width - 4))
                    bg_ansi = "\033[48;2;12;28;12m"
                    box_lines = []
                    
                    if lang:
                        lang_tag = f" {lang.upper()} "
                        header_text = "  " + co(C.BOLD + C.GREEN, lang_tag)
                        box_lines.append(pad_line(header_text, width, bg_ansi))
                    else:
                        box_lines.append(pad_line("  ", width, bg_ansi))
                    
                    for line in highlighted.splitlines():
                        box_lines.append(pad_line("    " + line, width, bg_ansi))
                    box_lines.append(pad_line("  ", width, bg_ansi))
                    highlighted_box = "\n".join(box_lines) + "\n"
                    
                    lines_up = self._code_line_count + 1
                    for _ in range(lines_up):
                        sys.stdout.write("\033[F\033[K")
                    sys.stdout.write(highlighted_box)
                    
                    self._code_lines = []
                    self._code_lang = ""
                    self._code_line_count = 0
                sys.stdout.flush()
                continue

            if not self.in_code_block and self.line_start and char == "#":
                count = 0
                while i < len(text) and text[i] == "#":
                    count += 1
                    i += 1
                self.header_level = count
                sys.stdout.write(co(C.BOLD + C.CYAN, "#" * count))
                sys.stdout.flush()
                continue

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

            if not self.in_code_block and self.line_start:
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
                if char == ">" and i + 1 < len(text) and text[i + 1] == " ":
                    sys.stdout.write(C.DIM + C.WHITE + "▎")
                    i += 2
                    self.line_start = False
                    sys.stdout.flush()
                    continue
                if (
                    char in ("-", "*", "_")
                    and i + 2 < len(text)
                    and text[i + 1] == char
                    and text[i + 2] == char
                ):
                    while i < len(text) and text[i] != "\n":
                        i += 1
                    sys.stdout.write(dim("─" * (min(92, 60))))
                    self.line_start = False
                    sys.stdout.flush()
                    continue
                if char == "|":
                    self._in_table = True
                    if i + 1 < len(text) and text[i + 1] == "-":
                        self._table_is_separator = True

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

            if char != " " and self.line_start:
                self.line_start = False

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

            if self._in_table and char == "|":
                char = "│"

            if self.in_code_block:
                self._code_line_buf += char

            sys.stdout.write(style + char)
            i += 1
        sys.stdout.flush()
