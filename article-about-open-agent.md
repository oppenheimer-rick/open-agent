# I Built an Open-Source AI Agent That Benchmarks Itself (And It's Actually Good)

**No API costs. No VC funding. Just 3,000 lines of Python and a llama.cpp backend.**

---

## The Problem With Every Agent Framework

I spent months testing LangChain, CrewAI, AutoGen, and the rest.

They all share the same DNA: **they're API wrappers dressed up as agents**. You configure a pipeline, wire it to GPT-4, and call it a day. The moment your credit card runs out, so does your agent.

And the benchmarks? Most frameworks cherry-pick numbers from someone else's paper. They don't run SWE-bench on their own code. They don't prove their agent can *actually* fix a bug in a repo it's never seen before.

I wanted something different.

A single-file agent that runs on my laptop. No API keys. No cloud dependencies. An agent that **benchmarks itself** using the same industry standards as OpenAI and DeepSeek — inside its own loop, not in some external harness that hides the cracks.

So I built one.

---

## Meet Open-Agent

Three thousand lines of Python. A single file. Twenty-four tools. Eleven REPL commands. Four benchmarks. Zero API costs.

```
loop.py  ─  The whole thing
benchmark/
  ├── bigcodebench.py    Code synthesis (1140 problems)
  ├── swebench.py        Software engineering (Docker eval)
  ├── agentic_bench.py   Multi-step tool use (10 tasks)
  └── gaia.py            Meta's reasoning benchmark
```

It doesn't wrap an API. It **is** the agent. Every tool, every system prompt, every context management trick — it's all right there in one file you can read, modify, and understand.

---

## How It Works

The core is a **ReAct loop** — Reason + Act, repeated until the task is done. But it's the details that matter.

### The Loop

```
1. System prompt → injected with your bio, preferences, and 24 tool definitions
2. Preflight    → maps your project, searches the web for context
3. Think        → LLM decides what to do next
4. Act          → executes a tool (edit a file, search the web, run Python)
5. Observe      → feeds the result back into context
6. Repeat       → until the task is complete
7. Return       → final message
```

Nothing revolutionary on paper. The magic is in what happens between the steps.

### Context Management That Actually Works

Small models (7B, 14B, 35B) fill their context window fast. The naive approach — keep appending turns until you hit the limit — works for about 20 minutes before the model forgets what it's doing.

Open-agent uses a **rolling window**:

```
┌─────────────────────────────────────────┐
│  System prompt          ─  always kept  │
│  Grounding context      ─  always kept  │
│  Memory / bio / prefs   ─  always kept  │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
│  Middle turns           ─  archived     │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
│  Last N turns           ─  preserved    │
└─────────────────────────────────────────┘
```

The first 3 messages (system, grounding, memory) stay. The last 9 turns stay. Everything in between gets compressed into a "Shadow Context" summary.

Result: the agent can run 500+ steps without losing the plot. On a 7B model.

### The Web-First Philosophy

Large language models are frozen in time. Yours, mine, everyone's. Their training data is at least six months old, often older.

Open-agent treats the web as its **primary reasoning engine**, not a fallback.

Every non-trivial task starts with `search_web` — not as a checkbox feature, but as a hard requirement embedded in the system prompt:

> *"You are FORBIDDEN from writing any implementation code during Step 1 and Step 2. Your FIRST action MUST be to call search_web."*

This discipline — research first, code second — is what makes small models punch above their weight. A 35B model with good search results beats a 70B model guessing from memory.

---

## The 24 Tools

Tools are the agent's hands. Each one is a Python function registered as an LLM callable via function calling.

### File Operations
| Tool | What it does |
|------|-------------|
| `read_file_section` | Reads 20-50 lines (context discipline baked in) |
| `write_file` | Creates new files |
| `patch_file` | Precision edits — no full rewrites |
| `outline_file` | Scans structure without reading content |

### Search
| Tool | What it does |
|------|-------------|
| `search_web` | SearXNG + Mojeek fallback, multi-variant |
| `web_fetch` | Downloads pages, smart-slices first 1000 lines |
| `scout_website` | Recursive doc hub extraction |
| `grep_codebase` | Regex across the project |
| `graph_search` | AST-level symbol lookup |

### Execution
| Tool | What it does |
|------|-------------|
| `run_python` | Sandboxed execution (30s timeout) |
| `run_bash` | Any shell command |
| `git_status` | Check what changed |

### Planning & Memory
| Tool | What it does |
|------|-------------|
| `todo_write / read / update` | Mission-critical plan tracking |
| `memory_save / load` | Persistent session facts |
| `consolidate_goals` | Scans memory, triggers deep research |
| `summarize_progress` | Shadow Context compression |

### Meta & Self-Improvement
| Tool | What it does |
|------|-------------|
| `sentinel_map_codebase` | Global project blueprint |
| `skill_factory` | Records patterns as reusable skills |
| `load_skill` | Fetches skill definitions |
| `verify_syntax` | Catches hallucinated syntax errors |

---

## The Part That Actually Excites Me: Self-Benchmarking

Every agent framework claims performance numbers. Almost none of them **run their own benchmarks inside their own agent loop**.

Open-agent does.

```python
from benchmark.bigcodebench import run_benchmark

# Same function used in interactive mode
run_benchmark(max_instances=50, subset="hard")
```

Or from the REPL:

```
/benchmark bigcodebench --instances 50 --subset hard
```

The agent calls `run_agent()` — the same function you use in interactive mode — on every benchmark problem. Same tools. Same context management. Same system prompts. No subprocess. No wrapper. No cheating.

### Four Benchmarks

**BigCodeBench** — 1,140 code synthesis problems with embedded unittest test cases. Used by Qwen and DeepSeek. Evaluated locally — no external package needed.

**SWE-bench Lite** — 300 real GitHub bugs from 12 popular Python repos. The agent clones each repo, explores the codebase, applies a fix, and produces a git patch. Evaluated with swebench's official Docker harness.

**Agentic Bench** — 10 deterministic tool-use tasks: build an OpenAI-compatible proxy for llama.cpp, a model router, a log analyser, a context window visualiser, a skill generator. Everything about self-hosted LLM infrastructure.

**GAIA** — Meta's gold standard for multi-step reasoning. The agent searches the web, downloads files, processes data, and synthesises answers. Requires HuggingFace auth.

Each benchmark module is a standalone file:

```
benchmark/
  bigcodebench.py    ←  imports run_agent() directly
  swebench.py        ←  imports run_agent() in cloned repo
  agentic_bench.py   ←  imports run_agent() in temp dir
  gaia.py            ←  imports run_agent() directly
```

**No dispatcher layer. No CLI runner. No abstraction indirection.** Each benchmark is a self-contained function you can call from Python or from the REPL.

---

## What 3,000 Lines Buys You

| Feature | Count |
|---------|-------|
| Lines of Python | 3,062 |
| LLM-callable tools | 24 |
| REPL commands | 11 |
| System prompts | 2 (general + coding) |
| Benchmarks | 4 |
| Search backends | 2 (SearXNG + Mojeek) |
| Context window | Rolling (12-turn sliding) |
| File watcher | Bidirectional (editor ↔ agent) |
| API cost | Zero |

It runs on llama.cpp at localhost:8083. It falls back to any OpenAI-compatible endpoint. It never pays per token.

---

## Why Open Source Matters Here

The agent framework space is crowded with:

- **Vendor playthings** — frameworks designed to sell you API credits
- **Academic prototypes** — papers with GitHub repos that haven't been touched in months
- **Configuration nightmares** — YAML files for days

Open-agent is none of those.

It's a single Python file you can read in an afternoon. It doesn't hide complexity behind abstractions — it puts everything in the open. The benchmarks are real. The evaluation is honest. The tools are practical.

And because it's a single file, you can fork it, gut it, rewrite the system prompts, add your own tools, and understand every line that runs on your machine.

---

## The Roadmap

What comes next:

- **Multi-agent orchestration** — spawn sub-agents for parallel research
- **Vision tools** — process screenshots and diagrams
- **Long-term memory** — vector store for cross-session recall
- **WebSocket bridge** — attach to VS Code as a copilot alternative

But the foundation is already solid. An agent that runs locally, works reliably, and tells you honestly how it performs.

---

## Try It

```bash
git clone https://github.com/your-username/open-agent
cd open-agent
pip install -r requirements.txt

# Start llama.cpp on port 8083
# Then:
python loop.py
```

For the benchmarks:

```bash
python -m benchmark.bigcodebench --instances 10
python -m benchmark.swebench --instances 5
python -m benchmark.agentic_bench

# Or from inside the REPL
# /benchmark bigcodebench --instances 10
```

---

*Built with llama.cpp, Python, and the unshakeable belief that local AI is the future. No API keys were harmed in the making of this agent.*
