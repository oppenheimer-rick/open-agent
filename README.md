<div align="center">
<br/>

<pre>
┌─────────────────────────────────────────────────────┐
│  ░▒▓ █▀█ █▀█ █▀▀ █▄ █ ▄▄ ▄▀█ █▀▀ █▀▀ █▄ █ ▀█▀ ▓▒░   │
│  ░▒▓ █▄█ █▀▀ ██▄ █ ▀█    █▀█ █▄█ ██▄ █ ▀█  █  ▓▒░   │
└─────────────────────────────────────────────────────┘
</pre>

### `local-first · privacy-friendly · intelligence-driven`

<br/>

[![License: MIT](https://img.shields.io/badge/License-MIT-00ff88.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-00cfff.svg?style=flat-square)](https://python.org)
[![llama.cpp](https://img.shields.io/badge/Inference-llama.cpp-ff6e00.svg?style=flat-square)](https://github.com/ggml-org/llama.cpp)
[![Ollama](https://img.shields.io/badge/Inference-Ollama-0052ff.svg?style=flat-square)](https://ollama.com)
[![Architecture](https://img.shields.io/badge/Architecture-Loop--Driven-ff0077.svg?style=flat-square)](#-the-loop-driven-ecosystem)

</div>

<div align="center">

> *"The open-source agent that stays on your machine."*

</div>

## ◈ What is open-agent?

`open-agent` is a professional-grade autonomous agent framework that turns your local machine into a **private, high-performance terminal IDE**. 

No cloud. No data leakage. No compromise. Works with any local LLM (llama.cpp, Ollama, vLLM).

## ⚡ Quick Start

### Option A: Automatic Curl Installer (Recommended)
```bash
curl -fsSL https://raw.githubusercontent.com/oppenheimer-rick/open-agent/main/install.sh | bash
```
Once installed, run the agent using:
```bash
openagent
# or simply:
op
```

### Option B: pip install
```bash
pip install open-agent-cli

# Run
openagent
```

### Option C: Source + venv
```bash
git clone https://github.com/oppenheimer-rick/open-agent
cd open-agent
make venv
source venv/bin/activate

# Run
openagent
```

## ◈ Model Guide — The Intelligence Engine

Autonomous agent behavior demands a model capable of sustained reasoning and precise tool orchestration. Below is the recommended configuration.

### ✦ Recommended: Qwen 2.5 Coding/Reasoning models (e.g. Qwen 2.5 7B/14B/32B/72B)

*Current gold standard for local reasoning and tool use.*

Start your local backend (llama.cpp server example):
```bash
./build/bin/llama-server \
-m /path/to/qwen-2.5-coder.gguf \
--host 0.0.0.0 \
--port 8083 \
-c 16384 \
--flash-attn on \
--cont-batching \
--temp 0.7
```

## ◈ Core Capabilities

### ◆ Built-in Tool Suite

<div align="center">

| Tool | Function |
|:---|:---|
| `⬡ read_file` | Read files in surgical chunk-sizes |
| `⬡ write_file` | Create new files and write code |
| `⬡ patch_file` | Surgical diff/patch-based editing |
| `⬡ insert_lines` | Precision line insertions |
| `⬡ replace_lines` | Precision line replacements |
| `⬡ delete_lines` | Precision line deletions |
| `⬡ run_bash` | Execute local terminal commands |
| `⬡ run_python` | Run code snippets for validation |
| `⬡ search_web` | Real-time multi-query web search |

</div>

### ◆ Slash Commands & Shortcuts

| Command | Action |
|---|---|
| `/help` | List all commands and shortcuts |
| `/history` | Browse past sessions, load one to continue |
| `/coding <task>` | Run task in structured coding mode |
| `/status` | Show endpoint URLs and file paths |
| `/memory <query>` | Search persistent memory |
| `/tools [index]` | Browse or expand tool execution results |
| `/resume` | Immediately continue the most recent session |
| `/session <id>` | Load a session by ID |
| `/new` | Start a fresh session |
| `/ootb [status\|render\|info]` | View the dynamic context layer |
| `/mission [init\|focus\|add\|status\|clear]` | Manage mission objectives |
| `/search <query>` | Single-shot web search |
| `/search-sessions <query>` | Full-text search across all saved sessions |

| Key | Action |
|---|---|
| `Ctrl+O` | Show tool execution history |
| `Ctrl+N` | New conversation |
| `Ctrl+/` | Show shortcuts help |
| `Escape` | Close modal |
| `Enter` | Send message |
| `Shift+Enter` | New line in composer |
| `ArrowUp` | Edit last message |

## ◈ The Skill-Driven Ecosystem

`open-agent` is designed to grow. Drop a `SKILL.md` (e.g. from a URL or local path) to teach your agent new workflows. Use `/skills` or `/load-skill <url>` inside the CLI session.

---

<div align="center">
  
<pre>
╔═══════════════════════════════════════════════════════════╗
║  MIT License · github.com/oppenheimer-rick/open-agent     ║
╚═══════════════════════════════════════════════════════════╝
</pre>

*Built for the community. Runs on your machine. Owned by you.*

</div>
