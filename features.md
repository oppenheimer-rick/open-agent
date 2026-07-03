# Open-Agent Features

## Dynamic Context Layer (`out_of_the_box.py`)
- **Persistent mission tracking** — Mission statement, active/pending/completed objectives, and critical user info survive across sessions.
- **LLM-generated insights** — The LLM feeds observations back into the context layer, building a rich user profile over time.
- **Self-improving** — Every conversation enriches the context. Functions like `add_insight()` and `tool_analyze_and_improve()` let the LLM autonomously update its understanding.
- **Render for system prompts** — `render()` produces a clean block injected into every conversation start.
- **Short status line** — `render_short()` provides a one-line summary for the terminal toolbar.

## Session History
- **Auto-save every session** — Every `run_agent` call persists the full message history to `~/.agentic-loop/sessions/`.
- **`/history` with interactive picker** — Browse recent sessions by ID, number, or search query.
- **`/session <id>`** — Direct session load by ID.
- **`/search-sessions <query>`** — Full-text search across all saved conversations.
- **`/rename-session <id> <name>`** — Give sessions meaningful names.
- **Auto-cleanup** — Oldest sessions beyond 50 are pruned automatically.

## Smart Web Search
- **Multi-query generation** — `smart_search(topic)` generates 3 targeted search queries exploring different angles.
- **Aggregated results** — Runs all queries, deduplicates by URL, ranks by score, returns top 12 results.
- **Graceful fallback** — Falls back to a single `search_web` call if the LLM fails to produce queries.

## Precision File Editing
- **`insert_lines(path, line, content)`** — Insert content at a specific line number.
- **`delete_lines(path, start, end)`** — Delete a range of lines by number.
- **`replace_lines(path, start, end, content)`** — Replace a range of lines with new content.
- **`patch_file(path, search, replace)`** — Classic search-and-replace patching.
- **`resume_write(path, content)`** — Append content with automatic overlap detection to avoid duplication from truncated writes.

## Code Validation
- **`validate_code(path)`** — Comprehensive syntax checking:
  - **Python**: `ast.parse` with line-numbered error reporting
  - **JavaScript**: `node -c` validation
  - **HTML**: Tag-balance analysis + truncated-file detection
  - **JSON**: `json.loads` with line-numbered errors
- **Post-write enforcement** — System prompt instructs the agent to call `validate_code` after every file write.

## Rich Terminal Rendering
- **Tables with box-drawing characters** — `| col | col |` rendered as `│ col │ col │`.
- **Bullet lists** — `- item` and `* item` rendered with `•` bullets.
- **Blockquotes** — `> quote` rendered with `▎` prefix in dim styling.
- **Italic and bold** — Rendered with distinct terminal styling.
- **Code block syntax highlighting** — Pygments-powered ANSI highlighting with Monokai theme.
- **Horizontal rules** — `---` rendered as a dim line.

## Tool History
- **`/tools` command** — Lists all tool results with status icons and argument summaries.
- **`/tools <index>`** — Re-displays a specific tool result in full detail.
- **`Ctrl+O`** — Instant shortcut to show tool execution history.

## Persistent Mission State
- **`/mission` slash command** — `init`, `focus`, `add`, `status`, `clear` subcommands.
- **Displayed once per session** — Mission status is shown at session start, not duplicated on every step.
- **Shadow context integration** — Mission state written to `.mission_state.txt`.

## Advanced ReAct Loop
- **Thinking blocks** — Native support for reasoning/thinking tokens, rendered with collapsible sections.
- **Context-efficient reading** — Reads files in 20-50 line chunks using `read_file_section`.
- **Multi-Search Preflight** — Lightweight preflight search for tasks with external-reference keywords.
- **Loop detection** — Automatically detects repetitive tool calls and breaks the loop.

## Core Tools
- **Skill Loader** — Fetch `SKILL.md` definitions from GitHub URLs.
- **Graph Search** — AST-level symbol navigation.
- **Persistent Memory** — Keyword-searchable JSONL storage with human-readable history.
- **Sandboxed Execution** — `run_bash` and `run_python` with timeout and output capture.
- **Bidirectional TUI Sync** — Filesystem watcher detects external editor changes.
- **Web Fetch & Scouting** — `web_fetch` for page content, `scout_website` for documentation deep-dives.
- **Git Integration** — `git_status` for repo awareness.

## UI/UX Features
- **KV Cache Pre-Warming** — Sends an invisible pre-warm request (`"Reply with 'yes' if you are here for me"`) containing system prompts/grounding behind a `Booting LLM...` spinner at session start. This pre-fills the local LLM server's KV cache, shifting the initial prompt prefill latency to the boot sequence so that subsequent user interactions generate responses instantly.
- **Live streaming spinner** — Animated spinner during LLM reasoning.
- **Token usage footer** — Per-step and per-session token stats.
- **Colour-coded output** — CYAN for assistant, GRAY for thinking, GREEN for success, RED for errors.
- **Compact tool logs** — One-line status indicators for file operations.
- **Auto-scroll** — Scroll-to-bottom on new content.

## Media & Playback Integration
- **`/play <query_or_url>`** — Play video/audio offline. Automatically searches and downloads the media via `yt-dlp`, then boots the player.
- **Background Player Controller** — Press `Ctrl+B` or `b` to background a song, returning to the REPL.
- **Interactive Socket IPC Menu** — Press `Ctrl+B` in the REPL to pull up a prompt controlling pause, resume, stop, or foregrounding `[f]`.
- **Media Dashboard** — Separate status listener using UNIX sockets to track player status.

## Autonomous Job Search Engine
- **`/job-search <resume_path>`** — Match resume with online jobs. Parses the CV, scrapes vacancy websites, identifies fits, and outputs matching roles.

## Browser Automation Tool
- **`browse_web(url, selector, action, value)`** — Interact with web pages headless using Playwright. Supports text scraping (`scrape`), clicking (`click`), and form inputs (`fill`).

## Self-Update Feature
- **`/update` (REPL) and `--update` (CLI)** — Automatically update open-agent. Executes `git pull` inside `~/.openagent` and updates dependencies with `pip install -e .`.
