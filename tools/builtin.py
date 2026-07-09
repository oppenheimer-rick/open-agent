import os
import re
import ast
import subprocess
import time
import json
from pathlib import Path
from collections import Counter

from ui.console import C, co, dim, trunc
from core.state import ACTIVE_FILES, AGENT_MODIFIED_FILES, ACTIVE_MESSAGES
from web_search import (
    search_web as ext_search_web,
    web_fetch as ext_web_fetch,
    scout_website as ext_scout_website,
    search_second_brain as ext_search_second_brain,
)

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

def count_lines(path: str) -> str:
    """Count the number of lines and characters in a file quickly without loading its full content into context."""
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: '{path}' does not exist"
    try:
        content = p.read_text(errors="replace")
        lines = content.count("\n")
        if content and not content.endswith("\n"):
            lines += 1
        chars = len(content)
        return f"File '{path}' has {lines} lines ({chars} characters)."
    except Exception as e:
        return f"ERROR: Could not count lines in '{path}': {e}"

def write_file(path: str, content: str) -> str:
    """Write full content to a file (use patch_file for edits)"""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    AGENT_MODIFIED_FILES[str(p.absolute())] = time.time()
    ACTIVE_FILES.add(str(p.absolute()))
    lines = content.count("\n") + 1
    return f"Written: {path}  ({lines} lines)"

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

    old_lines = original.splitlines()
    new_lines = updated.splitlines()
    search_lines = search.strip().splitlines() if search.strip() else []
    replace_lines = replace.strip().splitlines() if replace.strip() else []
    diff_lines = []

    for ln in search_lines:
        diff_lines.append(f"{co(C.RED, '  -' + ln)}")
    for ln in replace_lines:
        diff_lines.append(f"{co(C.GREEN, '  +' + ln)}")

    diff_output = "\n".join(diff_lines[:30])
    if len(diff_lines) > 30:
        diff_output += f"\n{co(C.GRAY, '  ... ' + str(len(diff_lines) - 30) + ' more lines')}"

    return f"Patched: {path}  ({old_n} → {new_n} lines, {delta})\n{diff_output}"

def append_file_chunk(path: str, content: str) -> str:
    """Append a chunk of text/code to the end of a file. Useful for writing large files in stages to avoid token limits."""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    exists = p.exists()
    try:
        if exists:
            current = p.read_text(errors="replace")
            if current and not current.endswith("\n"):
                current += "\n"
            p.write_text(current + content)
            action = "Appended chunk to"
        else:
            p.write_text(content)
            action = "Created and wrote initial chunk to"
            
        AGENT_MODIFIED_FILES[str(p.absolute())] = time.time()
        ACTIVE_FILES.add(str(p.absolute()))
        
        lines_added = content.count("\n") + 1
        total_lines = p.read_text(errors="replace").count("\n") + 1
        return f"SUCCESS: {action} {path} (+{lines_added} lines, total {total_lines} lines)"
    except Exception as e:
        return f"ERROR: Failed to append chunk to '{path}': {e}"

def save_last_code_block(path: str, block_index: int = 0) -> str:
    """
    Extract a markdown code block from the most recent assistant message in the conversation history
    and save it to a file. Extremely efficient—saves you from having to rewrite code you already generated.
    """
    if not ACTIVE_MESSAGES:
        return "ERROR: No active conversation history found."
        
    assistant_msg = None
    for msg in reversed(ACTIVE_MESSAGES):
        if msg.get("role") == "assistant" and msg.get("content"):
            content = msg["content"]
            if "```" in content:
                assistant_msg = content
                break
                
    if not assistant_msg:
        return "ERROR: No code block (```) found in any recent assistant messages."
        
    pattern = r"```[a-zA-Z0-9+#_-]*\n(.*?)\n```"
    code_blocks = re.findall(pattern, assistant_msg, re.DOTALL)
    
    if not code_blocks:
        pattern_fallback = r"```[a-zA-Z0-9+#_-]*([\s\S]*?)```"
        code_blocks = re.findall(pattern_fallback, assistant_msg)
        
    if not code_blocks:
        return "ERROR: Could not parse any code blocks from the assistant message."
        
    num_blocks = len(code_blocks)
    if block_index < 0:
        idx = num_blocks + block_index
    else:
        idx = block_index
        
    if idx < 0 or idx >= num_blocks:
        return f"ERROR: Invalid block_index {block_index}. Found {num_blocks} code block(s)."
        
    code_content = code_blocks[idx].strip()
    
    try:
        p = _safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code_content)
        
        AGENT_MODIFIED_FILES[str(p.absolute())] = time.time()
        ACTIVE_FILES.add(str(p.absolute()))
        
        lines = code_content.count("\n") + 1
        return f"SUCCESS: Extracted code block {idx} and saved to '{path}' ({lines} lines)."
    except Exception as e:
        return f"ERROR: Failed to save code block to '{path}': {e}"

def find_files(pattern: str, path: str = ".") -> str:
    """Find files matching a glob pattern (e.g., '*.py', '**/views.py') under a directory."""
    root = Path(path)
    if not root.exists():
        return f"ERROR: Directory '{path}' does not exist"
    try:
        matches = []
        SKIP = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache"}
        for p in root.rglob(pattern):
            if any(part in SKIP for part in p.parts):
                continue
            if p.is_file():
                matches.append(str(p))
        if not matches:
            return f"No files matching '{pattern}' found under '{path}'."
        out = "\n".join(matches[:50])
        if len(matches) > 50:
            out += f"\n… ({len(matches) - 50} more matches)"
        return out
    except Exception as e:
        return f"ERROR searching files: {e}"

def get_git_diff(path: str = ".") -> str:
    """Show the uncommitted git changes (git diff) for a directory or file."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: Path '{path}' does not exist"
    try:
        res = subprocess.run(
            ["git", "diff", str(p)], capture_output=True, text=True, timeout=10
        )
        if res.returncode != 0:
            return f"ERROR running git diff: {res.stderr}"
        diff = res.stdout
        if not diff.strip():
            return "No uncommitted changes detected (git diff is empty)."
        if len(diff) > 10000:
            diff = diff[:10000] + "\n... [diff truncated for context length] ..."
        return diff
    except Exception as e:
        return f"ERROR: {e}"

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
    """Fast local symbol search for small-context models."""
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

def validate_code(path: str) -> str:
    """Validate a file's syntax with detailed diagnostics."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"

    ext = p.suffix.lower()
    content = p.read_text(errors="replace")
    issues = []

    if ext == ".py":
        try:
            ast.parse(content)
            issues.append("✓ Python syntax: VALID")
        except SyntaxError as e:
            issues.append(f"✗ Python syntax error (line {e.lineno}): {e.msg}")
            issues.append(f"  Text: {e.text}")
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
        open_tags = re.findall(r"<(\w+)[^>]*>", content)
        close_tags = re.findall(r"</(\w+)>", content)

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

import tempfile
import httpx
from datetime import datetime

TODO_FILE = ".agent_todo.json"
PYTHON_TIMEOUT = 30

# Web search and fetch wrappers
def search_web(query: str, max_results: int = 5) -> str:
    """Search SearXNG web interface."""
    return ext_search_web(query, max_results)

def web_fetch(url: str) -> str:
    """Fetch raw markdown from a URL."""
    return ext_web_fetch(url)

def scout_website(url: str) -> str:
    """Scout website with full page structure metadata."""
    return ext_scout_website(url)

def search_second_brain(query: str) -> str:
    """Search locally cached knowledge database."""
    return ext_search_second_brain(query)

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
        try:
            subprocess.check_output(["node", "-c", path], stderr=subprocess.STDOUT)
            return "Syntax OK."
        except FileNotFoundError:
            return "No Node.js installed to check JS syntax. Manual observation recommended."
        except subprocess.CalledProcessError as e:
            return f"SYNTAX ERROR:\n{e.output.decode()}"
    return "No linter available for this file type."

def summarize_progress(summary: str) -> str:
    """Save a state-of-the-mission report to Shadow Context."""
    Path(".mission_state.txt").write_text(summary, encoding="utf-8")
    return "Shadow Context updated successfully."

def todo_read() -> str:
    if not Path(TODO_FILE).exists():
        return "[]"
    return Path(TODO_FILE).read_text(encoding="utf-8")

def todo_write(todos_json: str) -> str:
    try:
        todos = json.loads(todos_json)
        Path(TODO_FILE).write_text(json.dumps(todos, indent=2), encoding="utf-8")
        return f"Saved {len(todos)} todo(s) to {TODO_FILE}"
    except json.JSONDecodeError as e:
        return f"ERROR: Invalid JSON — {e}"

def todo_update(item_id: str, status: str) -> str:
    if not Path(TODO_FILE).exists():
        return f"ERROR: No todo file at {TODO_FILE}"
    todos = json.loads(Path(TODO_FILE).read_text(encoding="utf-8"))
    for t in todos:
        if str(t.get("id")) == str(item_id):
            t["status"] = status
            t["updated_at"] = datetime.now().isoformat(timespec="seconds")
            Path(TODO_FILE).write_text(json.dumps(todos, indent=2), encoding="utf-8")
            return f"Todo {item_id} → {status}"
    return f"ERROR: Todo id='{item_id}' not found"

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
    import sys
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
            content = p.read_text(encoding="utf-8")
        skills_dir = Path("skills")
        skills_dir.mkdir(exist_ok=True)
        if path_or_url.startswith(("http://", "https://")):
            name = path_or_url.rstrip("/").split("/")[-1] or "skill.md"
        else:
            name = Path(path_or_url).name
        dest = skills_dir / name
        dest.write_text(content, encoding="utf-8")
        return f"SUCCESS: Skill loaded from '{path_or_url}' → '{dest}'"
    except Exception as e:
        return f"ERROR loading skill: {e}"

def skill_factory(task_name: str, pattern_description: str) -> str:
    """Self-Improvement: Records a successful information-gathering pattern into a new built-in skill."""
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
    path.write_text(content, encoding="utf-8")
    from memory import save as memory_save
    memory_save(f"Created new skill: {task_name} at {path}", kind="skill_creation")
    return f"SUCCESS: Skill '{task_name}' factory-built at {path}. Use load_skill to apply it."

def consolidate_goals() -> str:
    """The Consolidator: Proactively scans memory for user worries/goals and triggers deep research."""
    from memory import load as memory_load
    worries = memory_load(
        "worry goal need earn money weight routine health finance", max_results=10
    )
    if worries == "[]":
        return "Consolidator: No urgent goals or worries found in recent memory."

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

def tail_file(path: str, n: int = 20) -> str:
    """TOOL: Read the tail end of a file (last N lines)."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
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
        lines = p.read_text(encoding="utf-8").splitlines()
        new_lines = content.splitlines()
        idx = max(0, min(line_number - 1, len(lines)))
        lines[idx:idx] = new_lines
        p.write_text("\n".join(lines), encoding="utf-8")
        return f"Inserted {len(new_lines)} line(s) at line {line_number}. File now has {len(lines)} lines."
    except Exception as e:
        return f"ERROR: {e}"

def delete_lines(path: str, start_line: int, end_line: int) -> str:
    """TOOL: Delete a range of lines (inclusive)."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if start_line < 1 or end_line > len(lines):
            return f"ERROR: Line range {start_line}-{end_line} out of bounds (file has {len(lines)} lines)"
        removed = lines[start_line - 1 : end_line]
        del lines[start_line - 1 : end_line]
        p.write_text("\n".join(lines), encoding="utf-8")
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
        lines = p.read_text(encoding="utf-8").splitlines()
        if start_line < 1 or end_line > len(lines):
            return f"ERROR: Line range {start_line}-{end_line} out of bounds (file has {len(lines)} lines)"
        new_lines = content.splitlines()
        lines[start_line - 1 : end_line] = new_lines
        p.write_text("\n".join(lines), encoding="utf-8")
        return (
            f"Replaced lines {start_line}-{end_line} with {len(new_lines)} line(s). "
            f"File now has {len(lines)} lines."
        )
    except Exception as e:
        return f"ERROR: {e}"

def resume_write(path: str, new_content: str) -> str:
    """TOOL: Append or complete a file that was truncated during write_file."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}. Use write_file first."

    try:
        current = p.read_text(encoding="utf-8")
        overlap = 0
        new_lines = new_content.splitlines()
        existing_lines = current.splitlines()

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

        if append_content and not any(
            append_content.rstrip().endswith(s)
            for s in [">", "}", ";", "```", '"', "')"]
        ):
            p.write_text(current + "\n" + append_content, encoding="utf-8")
            return (
                f"Appended {len(append_content)} chars. "
                f"WARNING: New content may also be truncated (no clear ending)."
            )
        else:
            p.write_text(current + "\n" + append_content, encoding="utf-8")
            return (
                f"Resumed writing to {path}. "
                f"Added {len(append_content)} chars after dedup overlap of {overlap} lines."
            )
    except Exception as e:
        return f"ERROR: {e}"


def auto_patch_error(path: str, error_message: str, hint: str = "") -> str:
    """Locate and repair troublesome lines/errors in a file based on compiler/traceback messages.
    Token-efficient: reads only error-surrounding code context, queries the LLM for a search/replace patch,
    applies the patch, and runs syntax verification.
    """
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: File '{path}' does not exist."

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: Could not read file '{path}': {e}"

    lines = content.splitlines()
    total_lines = len(lines)

    # Parse line numbers from error_message
    line_numbers = []
    file_basename = p.name
    # Search for line numbers specific to the target file
    file_specific_patterns = [
        rf"{re.escape(file_basename)}[^\n]*?(?:line\s*|L|:)\s*(\d+)",
        rf"(?:line\s*|L|:)\s*(\d+)[^\n]*?in[^\n]*?{re.escape(file_basename)}"
    ]
    
    file_specific_lines = []
    for pat in file_specific_patterns:
        for m in re.finditer(pat, error_message, re.IGNORECASE):
            file_specific_lines.append(int(m.group(1)))

    if file_specific_lines:
        line_numbers = file_specific_lines
    else:
        # Fallback to any general line number matches
        for m in re.finditer(r"(?:line\s*|L|:)\s*(\d+)", error_message, re.IGNORECASE):
            val = int(m.group(1))
            if val < total_lines + 5:
                line_numbers.append(val)

    line_numbers = sorted(list(set(line_numbers)))
    context_chunks = []

    if not line_numbers:
        # No line numbers found; use full file if small, else chunks
        if total_lines <= 120:
            context_chunks.append((1, total_lines))
        else:
            context_chunks.append((1, 80))
            context_chunks.append((max(1, total_lines - 20), total_lines))
    else:
        # Build 8-line context windows around identified lines
        windows = []
        for ln in line_numbers:
            start = max(1, ln - 8)
            end = min(total_lines, ln + 8)
            windows.append((start, end))
        
        # Merge overlapping/adjacent windows
        windows.sort()
        merged = []
        for current in windows:
            if not merged:
                merged.append(current)
            else:
                prev_start, prev_end = merged[-1]
                curr_start, curr_end = current
                if curr_start <= prev_end + 3:
                    merged[-1] = (prev_start, max(prev_end, curr_end))
                else:
                    merged.append(current)
        context_chunks = merged

    # Format the code context
    context_lines = []
    for start, end in context_chunks:
        context_lines.append(f"--- lines {start} to {end} ---")
        for idx in range(start, end + 1):
            context_lines.append(f"{idx:>5}: {lines[idx-1]}")
    code_context = "\n".join(context_lines)

    system_prompt = (
        "You are a code repair assistant. Output a JSON object with 'search', 'replace', and 'explanation' keys to patch the file.\n"
        "Rules: 1. Keep 'search' exact. 2. Minimal diff. 3. No markdown code blocks."
    )

    user_prompt = (
        f"File: {path}\n"
        f"Error:\n{error_message}\n\n"
        f"Hint: {hint}\n\n"
        f"Context:\n{code_context}\n"
    )

    import providers
    llm_response = providers.generate(system_prompt, user_prompt, max_tokens=128)

    # Clean response
    clean_res = llm_response.strip()
    if clean_res.startswith("```"):
        lines_res = clean_res.splitlines()
        if lines_res[0].startswith("```"):
            lines_res = lines_res[1:]
        if lines_res and lines_res[-1].startswith("```"):
            lines_res = lines_res[:-1]
        clean_res = "\n".join(lines_res).strip()

    try:
        patch_data = json.loads(clean_res)
    except Exception:
        # Fallback regex extraction
        match_json = re.search(r"\{.*\}", clean_res, re.DOTALL)
        if match_json:
            try:
                patch_data = json.loads(match_json.group(0))
            except Exception:
                return f"ERROR: LLM output was not valid JSON. Response:\n{llm_response}"
        else:
            return f"ERROR: LLM output was not valid JSON. Response:\n{llm_response}"

    search_str = patch_data.get("search")
    replace_str = patch_data.get("replace")
    explanation = patch_data.get("explanation", "Auto patch applied.")

    if search_str is None or replace_str is None:
        return f"ERROR: JSON patch missing 'search' or 'replace' keys. Found: {patch_data}"

    occurrences = content.count(search_str)
    if occurrences == 0:
        return (
            f"ERROR: The search string generated by the LLM was not found in '{path}'.\n"
            f"Explanation of intended fix: {explanation}\n"
            f"Search string looked for (exact match needed):\n{repr(search_str)}"
        )
    if occurrences > 1:
        return (
            f"ERROR: The search string matched {occurrences} times in '{path}'. "
            f"It must match exactly once. Add more context surrounding the edit."
        )

    # Apply edit
    updated = content.replace(search_str, replace_str, 1)
    try:
        p.write_text(updated, encoding="utf-8")
    except Exception as e:
        return f"ERROR: Failed to write update to '{path}': {e}"

    AGENT_MODIFIED_FILES[str(p.absolute())] = time.time()
    ACTIVE_FILES.add(str(p.absolute()))

    syntax_res = verify_syntax(str(p))

    search_lc = search_str.count("\n") + 1
    replace_lc = replace_str.count("\n") + 1
    diff_val = replace_lc - search_lc
    diff_str = f"+{diff_val}" if diff_val >= 0 else str(diff_val)

    report = (
        f"SUCCESS: Auto-patched '{path}'\n"
        f"Explanation: {explanation}\n"
        f"Lines replaced: {search_lc} → {replace_lc} (delta {diff_str})\n"
        f"Syntax check: {syntax_res}\n\n"
        f"--- Diff applied ---\n"
    )
    for ln in search_str.splitlines():
        report += f"  - {ln}\n"
    for ln in replace_str.splitlines():
        report += f"  + {ln}\n"

    return report


