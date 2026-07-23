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
-ub 1024   
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
-m /path/to/models/Qwen3.6-35B-A3B-Q4-K-M.gguf  
--no-mmap  
-c 64000  
--n-gpu-layers auto
```

> [!TIP]
> **Zero-Latency Boot (KV Cache Pre-Warming)**: On starting a new session, open-agent automatically triggers a `Booting LLM...` spinner and sends an invisible pre-warm request to the local server. Combined with llama-server's `--cache-prompt` flag, this pre-fills the server's KV cache, moving the initial prompt processing overhead to the boot phase so your first real message gets a response almost instantly.

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
| `⬡ search_web` | Real-time multi-engine web search |
| `⬡ browse_web` | Headless browser automation (scrape, click, fill) using Playwright |

</div>

### ◆ Web Search Architecture

The agent uses a **5-tier sequential fallback chain** — each tier is tried in order until one returns results. Never parallel.

| Tier | Engine | Latency | Notes |
|:---|:---|:---:|:---|
| 1 | **ddgs** (DuckDuckGo API, 9k+ ⭐) | ~2.5s | Primary — persistent instance, VQD token cached |
| 2 | **DuckDuckGo Lite** | ~1s | Fresh httpx client per call (avoids rate-limit cookies) |
| 3 | **Wikipedia API** | ~0.5s | Never-fail fallback for factual/topical queries |
| 4 | **DDG HTML scrape** | ~2s | Deep fallback with VQD token |
| 5 | **SearXNG** (localhost) | ~2s | Single call, 2s timeout, no retries |

**Safety**: Input sanitized (200 char limit, control chars stripped), URLs validated (http/https only), HTML stripped from output.  
**Cache**: LRU with 5-min TTL for repeated queries. STOP/failure messages are **never** cached.  
**Failure mode**: Returns `STOP: ...` signal → agent loop detects it, injects a system message to halt all search/fetch calls, and breaks out of the step loop.

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
