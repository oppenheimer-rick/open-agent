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

### ✦ Recommended: Qwen 3.6 35B Coding/Reasoning models (e.g. Use MOE offloading if you have less VRAM)

*Current gold standard for local reasoning and tool use.*

Start your local backend (llama.cpp server example):
```bash
llama-server  
--host 127.0.0.1   
--port 8083   
--override-tensor 'blk\.(2[0-9]|3[0-9]|4[0-6])\.ffn_(gate_up|down)_exps\.weight=CPU'   
-b 1024   
-ub 256   
--cache-type-k q4_0   
--cache-type-v q4_0   
--flash-attn on   
--jinja   
--top-k 20   
--top-p 0.95   
--temp 1.0   
--repeat-penalty 1.0   
--presence-penalty 1.5   
--cache-prompt   
--reasoning auto  
--no-warmup 
--n-cpu-moe 19  
-m /path/to/models/Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf  
--no-mmap  
-c 28000  
--n-gpu-layers auto
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
| `⬡ browse_web` | Headless browser automation (scrape, click, fill) using Playwright |

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
| `/play <query>` | Play and stream YouTube music offline in a docked GUI window |
| `/job-search <resume>` | Analyze resume, scrape vacancies, and match jobs autonomously |
| `/update` | Automatically update open-agent source code and dependencies |

| Key | Action |
|---|---|
| `Ctrl+O` | Show tool execution history |
| `Ctrl+N` | New conversation |
| `Ctrl+B` | Trigger the background media playback controller menu |
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
