import json
import re
import subprocess
import shutil
import importlib.util
import sys
from pathlib import Path
import tools.builtin as builtin
from tools.builtin import PYTHON_TIMEOUT

# Tool results buffer
_tool_results_buffer = []

def tool_result_clear():
    global _tool_results_buffer
    _tool_results_buffer = []

def tool_result_append(name: str, args: dict, result, error: bool):
    global _tool_results_buffer
    _tool_results_buffer.append(
        {"name": name, "args": args, "result": result, "error": error}
    )

def tool_result_show(index: int = -1):
    global _tool_results_buffer
    if not _tool_results_buffer:
        return "No tools executed yet."
    if index >= len(_tool_results_buffer) or index < -len(_tool_results_buffer):
        return f"Index {index} out of range (total {len(_tool_results_buffer)})."
    item = _tool_results_buffer[index]
    status = "ERROR" if item["error"] else "SUCCESS"
    return (
        f"--- TOOL RESULT #{index} ({status}) ---\n"
        f"Tool: {item['name']}\n"
        f"Args: {json.dumps(item['args'], indent=2)}\n"
        f"Output:\n{item['result']}"
    )

def show_tool_history():
    global _tool_results_buffer
    if not _tool_results_buffer:
        return "No tools executed in this session."
    lines = ["--- TOOL EXECUTION HISTORY ---"]
    for i, item in enumerate(_tool_results_buffer):
        status = "✖" if item["error"] else "✓"
        lines.append(f"  #{i:<2} {status} {item['name']}")
    return "\n".join(lines)

def _safe_import_module(name: str, py_path: Path):
    """Import a Python module from a file path, proactively auto-installing missing dependencies."""
    import importlib.util
    import subprocess
    import sys
    import ast
    
    PACKAGE_MAP = {
        "yaml": "pyyaml",
        "bs4": "beautifulsoup4",
        "PIL": "pillow",
        "docx": "python-docx",
        "fitz": "pymupdf",
        "pg8000": "pg8000",
        "mysql": "mysql-connector-python",
        "dotenv": "python-dotenv",
        "jwt": "pyjwt",
        "openpyxl": "openpyxl",
        "pptx": "python-pptx",
    }
    
    try:
        code_text = py_path.read_text(encoding="utf-8")
        tree = ast.parse(code_text)
        extracted_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    extracted_imports.append(n.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                extracted_imports.append(node.module)
                
        for imp in extracted_imports:
            if not imp or imp.startswith("."):
                continue
                
            root_module = imp.split(".")[0]
            local_py = py_path.parent / f"{root_module}.py"
            local_dir = py_path.parent / root_module
            cwd_py = Path.cwd() / f"{root_module}.py"
            cwd_dir = Path.cwd() / root_module
            
            if local_py.exists() or local_dir.exists() or cwd_py.exists() or cwd_dir.exists():
                continue
                
            try:
                __import__(root_module)
            except (ImportError, ModuleNotFoundError):
                pip_name = PACKAGE_MAP.get(root_module, root_module)
                print(f"  📦 Missing dependency '{root_module}' detected. Auto-installing '{pip_name}'...")
                try:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", pip_name],
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    print(f"  ✓ Successfully installed '{pip_name}'.")
                except subprocess.CalledProcessError as err:
                    if "externally-managed-environment" in err.stderr or "externally-managed" in err.stderr:
                        print(f"  📦 Environment is externally managed. Retrying with --break-system-packages...")
                        try:
                            subprocess.run(
                                [sys.executable, "-m", "pip", "install", pip_name, "--break-system-packages"],
                                check=True,
                                capture_output=True,
                                text=True
                            )
                            print(f"  ✓ Successfully installed '{pip_name}' using override.")
                        except subprocess.CalledProcessError as err2:
                            print(f"  ✖ Failed to auto-install '{pip_name}' with override: {err2.stderr}")
                    else:
                        print(f"  ✖ Failed to auto-install '{pip_name}': {err.stderr}")
    except Exception as e:
        print(f"  ⚠ Dependency pre-scan failed: {e}")
        
    try:
        spec = importlib.util.spec_from_file_location(name, str(py_path))
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(py_path.parent))
        spec.loader.exec_module(module)
        return module
    finally:
        if str(py_path.parent) in sys.path:
            sys.path.remove(str(py_path.parent))

def load_dynamic_tools():
    """Scan dynamic_tools/ directory, load modules and register them to TOOLS and TOOL_MAP."""
    import json
    dynamic_dir = Path.cwd() / "dynamic_tools"
    if not dynamic_dir.exists():
        return
        
    for json_path in dynamic_dir.glob("*.json"):
        name = json_path.stem
        py_path = dynamic_dir / f"{name}.py"
        if not py_path.exists():
            continue
            
        try:
            schema = json.loads(json_path.read_text(encoding="utf-8"))
            module = _safe_import_module(name, py_path)
            func = getattr(module, name, None) or getattr(module, "run", None)
            if not func:
                continue
                
            if not any(t.get("function", {}).get("name") == name for t in TOOLS):
                TOOLS.append(schema)
            TOOL_MAP[name] = lambda a, f=func: f(**a)
        except Exception as e:
            print(f"  ⚠ Failed to load dynamic tool '{name}': {e}")

def synthesize_tool(name: str, description: str, parameters: dict, code: str, language: str = "python") -> str:
    """Synthesize and register a new custom Python, Go, C++, or C tool on the fly."""
    import json
    import re
    import subprocess
    import shutil
    
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        return "ERROR: Tool name must contain only letters, numbers, and underscores."
        
    if name in TOOL_MAP:
        return f"ERROR: A tool named '{name}' is already registered."
        
    language = language.lower().strip()
    if language not in ("python", "go", "cpp", "c"):
        return f"ERROR: Unsupported language '{language}'. Supported: python, go, cpp, c."
        
    try:
        dynamic_dir = Path.cwd() / "dynamic_tools"
        dynamic_dir.mkdir(exist_ok=True)
        
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters
            }
        }
        
        py_path = dynamic_dir / f"{name}.py"
        json_path = dynamic_dir / f"{name}.json"
        
        if language == "python":
            py_path.write_text(code, encoding="utf-8")
        else:
            binary_path = dynamic_dir / name
            
            if language == "go":
                source_path = dynamic_dir / f"{name}.go"
                source_path.write_text(code, encoding="utf-8")
                
                if not shutil.which("go"):
                    return "ERROR: Go compiler ('go') is not installed on the system."
                    
                comp = subprocess.run(
                    ["go", "build", "-o", str(binary_path), str(source_path)],
                    capture_output=True,
                    text=True
                )
                if comp.returncode != 0:
                    return f"ERROR: Go compilation failed:\n{comp.stderr}"
                    
            elif language == "cpp":
                source_path = dynamic_dir / f"{name}.cpp"
                source_path.write_text(code, encoding="utf-8")
                
                if not shutil.which("g++"):
                    return "ERROR: C++ compiler ('g++') is not installed on the system."
                    
                comp = subprocess.run(
                    ["g++", "-O3", "-std=c++17", "-o", str(binary_path), str(source_path)],
                    capture_output=True,
                    text=True
                )
                if comp.returncode != 0:
                    return f"ERROR: C++ compilation failed:\n{comp.stderr}"
                    
            elif language == "c":
                source_path = dynamic_dir / f"{name}.c"
                source_path.write_text(code, encoding="utf-8")
                
                if not shutil.which("gcc"):
                    return "ERROR: C compiler ('gcc') is not installed on the system."
                    
                comp = subprocess.run(
                    ["gcc", "-O3", "-o", str(binary_path), str(source_path)],
                    capture_output=True,
                    text=True
                )
                if comp.returncode != 0:
                    return f"ERROR: C compilation failed:\n{comp.stderr}"
            
            wrapper_code = f"""import subprocess
import json
from pathlib import Path

def run(**kwargs):
    binary_path = Path(__file__).parent / "{name}"
    args_json = json.dumps(kwargs)
    try:
        res = subprocess.run(
            [str(binary_path)],
            input=args_json,
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR executing binary: {{e.stderr.strip() or e}}"
"""
            py_path.write_text(wrapper_code, encoding="utf-8")
            
        json_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
        module = _safe_import_module(name, py_path)
        func = getattr(module, name, None) or getattr(module, "run", None)
        if not func:
            py_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            return f"ERROR: The generated code must define a function named '{name}' or 'run' as the entrypoint."
            
        if not any(t.get("function", {}).get("name") == name for t in TOOLS):
            TOOLS.append(schema)
        TOOL_MAP[name] = lambda a, f=func: f(**a)
        
        return (
            f"SUCCESS: Tool '{name}' ({language.upper()} binary) has been successfully synthesized, validated, and registered.\n"
            f"You can now invoke it on the next step with parameters: {list(parameters.get('properties', {}).keys())}."
        )
    except Exception as e:
        return f"ERROR synthesizing tool: {e}"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "synthesize_tool",
            "description": (
                "Create, compile, and register a new custom Python, Go, C++, or C tool on the fly. "
                "Use this tool when you need a specialized high-performance function (e.g., binary formats, CPU-heavy tasks). "
                "If using Go/C/C++, compile commands run automatically. Return values must be printed to standard output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique name of the new tool (alphanumeric and underscores only, e.g. 'minify_html')",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed description of what the tool does and when to use it",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "JSON schema definitions of the function's parameters (e.g. {'type': 'object', 'properties': {...}, 'required': [...]})",
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete source code. If using Python, the function name must match the tool name or 'run'. If using C/C++/Go, the entrypoint must be main().",
                    },
                    "language": {
                        "type": "string",
                        "description": "Programming language of the tool: 'python' (default), 'go', 'cpp', or 'c'",
                        "enum": ["python", "go", "cpp", "c"]
                    },
                },
                "required": ["name", "description", "parameters", "code"],
            },
        },
    },
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
            "name": "count_lines",
            "description": (
                "Quickly count the number of lines and characters in a file "
                "WITHOUT loading its content. Extremely fast and saves context tokens."
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
            "name": "append_file_chunk",
            "description": (
                "Append a chunk of text or code to the end of a file. "
                "Use this to write large files in stages, preventing token truncation "
                "and avoiding the need to regenerate the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute file path",
                    },
                    "content": {
                        "type": "string",
                        "description": "The text/code content chunk to append",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_last_code_block",
            "description": (
                "Extract a code block from your own previous assistant response and save it "
                "directly to a file path. Extremely efficient—saves you from having to rewrite "
                "the code you already generated in the tool call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to save the code to",
                    },
                    "block_index": {
                        "type": "integer",
                        "description": "0-based index of the code block in your message (default 0)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Search for files matching a glob pattern (e.g., '*.py', '**/models.py') under a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root directory to search (default '.')",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_git_diff",
            "description": "View uncommitted git changes (git diff) for the workspace or a specific file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory to diff (default '.')",
                    },
                },
                "required": [],
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
                "Search the web via SearXNG or DuckDuckGo fallback. "
                "Returns compact results (default 3). This model has a 61k context limit — "
                "keep max_results small (1-3) to avoid filling the window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "description": "Results to return (default 3, keep under 5)",
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
            "description": "Generate targeted search queries, run them, and aggregate results. Use for broader research. Default: 1 query to conserve the 61k context window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The research topic or question"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of search queries to generate (default 1, max 2 for 61k context)"
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


def smart_search(topic: str, count: int = 1) -> str:
    """
    TOOL: Generate targeted search query, run it,
    and return results."""
    queries = generate_search_queries(topic, count)
    if not queries:
        queries = [topic]

    all_results = []
    seen_urls = set()

    for q in queries:
        try:
            raw = search_web(q, max_results=2, current=True)
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
                        snippet_line[3:].strip()  # after "   " prefix
                        if snippet_line
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
        raw = search_web(topic, max_results=2, current=True)
        return raw

    output = [f"SMART SEARCH: {topic}"]
    for r in all_results[:3]:
        output.append(f"\n{r['title']}")
        output.append(f"  URL: {r['url']}")
        if r["snippet"]:
            output.append(f"  {r['snippet'][:120]}")

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

# Dispatcher mapping
TOOL_MAP = {
    "outline_file": lambda a: builtin.outline_file(a["path"]),
    "count_lines": lambda a: builtin.count_lines(a["path"]),
    "append_file_chunk": lambda a: builtin.append_file_chunk(a["path"], a["content"]),
    "save_last_code_block": lambda a: builtin.save_last_code_block(a["path"], int(a.get("block_index", 0))),
    "find_files": lambda a: builtin.find_files(a["pattern"], a.get("path", ".")),
    "get_git_diff": lambda a: builtin.get_git_diff(a.get("path", ".")),
    "read_file_section": lambda a: builtin.read_file_section(a["path"], a["start_line"], a["end_line"]),
    "write_file": lambda a: builtin.write_file(a["path"], a["content"]),
    "patch_file": lambda a: builtin.patch_file(a["path"], a["search"], a["replace"]),
    "grep_codebase": lambda a: builtin.grep_codebase(a["pattern"], a.get("path", "."), a.get("file_ext", ".py")),
    "graph_search": lambda a: builtin.graph_search(a["query"], a.get("path", "."), a.get("file_ext", ".py")),
    "search_web": lambda a: builtin.search_web(a["query"], int(a.get("max_results", 3))),
    "web_fetch": lambda a: builtin.web_fetch(a["url"]),
    "search_second_brain": lambda a: builtin.search_second_brain(a["query"]),
    "scout_website": lambda a: builtin.scout_website(a["url"]),
    "verify_syntax": lambda a: builtin.verify_syntax(a["path"]),
    "summarize_progress": lambda a: builtin.summarize_progress(a["summary"]),
    "run_bash": lambda a: builtin.run_bash(a["command"], int(a.get("timeout", builtin.PYTHON_TIMEOUT))),
    "run_python": lambda a: builtin.run_python(a["code"], int(a.get("timeout", builtin.PYTHON_TIMEOUT))),
    "memory_load": lambda a: __import__("memory").load(a["query"], int(a.get("max_results", 5))),
    "memory_save": lambda a: __import__("memory").save(a["note"], a.get("kind", "note")),
    "todo_read": lambda _: builtin.todo_read(),
    "todo_write": lambda a: builtin.todo_write(a["todos_json"]),
    "todo_update": lambda a: builtin.todo_update(a["item_id"], a["status"]),
    "git_status": lambda a: builtin.git_status(a.get("path", ".")),
    "load_skill": lambda a: builtin.load_skill(a["path_or_url"]),
    "sentinel_map_codebase": lambda _: __import__("tools.sentinel").sentinel_map_codebase(),
    "skill_factory": lambda a: builtin.skill_factory(a["task_name"], a["pattern_description"]),
    "consolidate_goals": lambda _: builtin.consolidate_goals(),
    "smart_search": lambda a: __import__("core.agent", fromlist=["smart_search"]).smart_search(a["topic"], int(a.get("count", 1))),
    "tail_file": lambda a: builtin.tail_file(a["path"], int(a.get("n", 20))),
    "insert_lines": lambda a: builtin.insert_lines(a["path"], int(a["line_number"]), a["content"]),
    "delete_lines": lambda a: builtin.delete_lines(a["path"], int(a["start_line"]), int(a["end_line"])),
    "replace_lines": lambda a: builtin.replace_lines(a["path"], int(a["start_line"]), int(a["end_line"]), a["content"]),
    "validate_code": lambda a: builtin.validate_code(a["path"]),
    "resume_write": lambda a: builtin.resume_write(a["path"], a["content"]),
    "tool_analyze_and_improve": lambda a: __import__("out_of_the_box").tool_analyze_and_improve(a["messages_snapshot"]),
    "tool_update_objective": lambda a: __import__("out_of_the_box").tool_update_objective(a["title"], a.get("status", "in_progress")),
    "tool_add_info": lambda a: __import__("out_of_the_box").tool_add_info(a["fact"]),
    "tool_mission": lambda a: __import__("out_of_the_box").tool_mission(a["statement"]),
    "youtube_search": lambda a: __import__("youtube_utils").youtube_search(a["query"], int(a.get("max_results", 5))),
    "youtube_fetch_transcript": lambda a: __import__("youtube_utils").youtube_fetch_transcript(a["video_id"]),
    "play_music": lambda a: __import__("youtube_utils").play_song(a["query_or_url"]),
    "browse_web": lambda a: __import__("web_search").browse_web(a["url"], a.get("selector", "body"), a.get("action", "scrape"), a.get("value", None)),
    "synthesize_tool": lambda a: synthesize_tool(a["name"], a["description"], a["parameters"], a["code"], a.get("language", "python")),
}

# Auto-load dynamic tools
try:
    load_dynamic_tools()
except Exception:
    pass
