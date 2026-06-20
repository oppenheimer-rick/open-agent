# Open-Agent Benchmarking Infrastructure

## 📋 Overview

Four industry-standard benchmarks, each a **standalone module** in `benchmark/` that imports
`run_agent()` directly from `loop.py`. Every problem runs inside the agent's own
ReAct loop — the same function used in interactive mode.

```
/benchmark bigcodebench     Code synthesis (unittest evaluation, 1140 problems)
/benchmark swebench         Software engineering (git patches + Docker eval)
/benchmark agentic-bench    Agent tool-use (10 deterministic tasks)
/benchmark gaia             Multi-step reasoning (requires HF auth)
```

## 🚀 Quick Start

```bash
# All benchmarks available via the REPL
/benchmark bigcodebench --instances 10
/benchmark swebench --instances 5
/benchmark agentic-bench
/benchmark gaia --instances 5

# Or run directly:
python -m benchmark.bigcodebench --instances 10
python -m benchmark.swebench --instances 5 --evaluate
python -m benchmark.agentic_bench
python -m benchmark.gaia --instances 5
```

## 📊 Benchmarks

### 1. SWE-bench Lite (Industry Gold Standard)
- **Used by:** OpenAI, Anthropic, DeepSeek, Google DeepMind
- **What it tests:** Agent patches real GitHub bugs from 12 popular Python repos
- **Format:** Clone repo → call `run_agent()` inside repo → capture git diff → Docker evaluation
- **Official evaluator:** `swebench` package v4.1.0 (Docker-based)
- **Module:** `benchmark/swebench.py`
- **Two-phase pipeline:**
  1. `generate_patches()` — agent patch generation
  2. `run_evaluation()` — Docker-based scoring via `swebench.harness`

```bash
python -m benchmark.swebench --instances 5
python -m benchmark.swebench --instances 5 --evaluate
```

### 2. BigCodeBench (Qwen/DeepSeek Standard)
- **Used by:** Qwen, DeepSeek
- **What it tests:** Code synthesis from natural language specs (1140 problems)
- **Split:** `v0.1.4` (latest, includes embedded test cases)
- **Format:** Load task → call `run_agent()` → extract code → **local unittest evaluation**
- **Module:** `benchmark/bigcodebench.py`

```bash
python -m benchmark.bigcodebench --instances 10
python -m benchmark.bigcodebench --instances 50 --subset hard
```

### 3. Agentic Tool-Use Benchmark (Local/Deterministic)
- **What it tests:** Multi-step agentic tasks: LLM proxy servers, model routers,
  log analysers, system monitors, quant config generators, skill generators
- **Format:** Setup isolated temp dir → call `run_agent()` → verify with exact assertions
- **Always works** — no API keys, no auth, no network
- **Module:** `benchmark/agentic_bench.py`

```bash
python -m benchmark.agentic_bench
```

### 4. GAIA (Multi-Step Reasoning)
- **What it tests:** Complex research tasks requiring web search + file processing + reasoning
- **Gold standard** from Meta AI for general agent intelligence
- **Requires:** HuggingFace authentication
- **Module:** `benchmark/gaia.py`

```bash
pip install huggingface_hub
huggingface-cli login
python -m benchmark.gaia --instances 5
```

## 📁 File Layout

```
agentic-loop/
├── loop.py                        ← Agent (already modified: run_agent returns final_message, accepts skip_preflight)
├── benchmark/
│   ├── __init__.py                ← Package marker with version
│   ├── config.py                  ← Shared configuration
│   ├── bigcodebench.py            ← Modular: code synthesis (imports run_agent)
│   ├── swebench.py                ← Modular: software engineering (imports run_agent)
│   ├── agentic_bench.py           ← Modular: tool-use benchmark (imports run_agent)
│   ├── gaia.py                    ← Modular: multi-step reasoning (imports run_agent)
│   ├── livecodebench.py           ← Modular: contamination-aware coding
│   ├── validate.py                ← Prediction file schema validation
│   ├── reports/                   ← Generated reports & evaluation results
│   └── README.md                  ← This file

Deprecated (thin wrappers — kept for backward compatibility):
├── bigcodebench_harness.py        → redirects to benchmark.bigcodebench
├── swebench_pro_harness.py        → redirects to benchmark.swebench
├── swebench_harness.py            → redirects to benchmark.swebench
├── benchmark_runner.py            → redirects to benchmark.bigcodebench
└── agentic_bench_local.py         → redirects to /benchmark agentic-bench
```

## 🔧 Adding a New Benchmark

1. Create `benchmark/<name>.py` — a standalone module that:
   - Imports `run_agent()` from `loop.py`
   - Defines a public `run_benchmark()` or equivalent function
   - Has `if __name__ == "__main__":` for direct CLI usage
2. Add an entry in `loop.py`'s `_cmd_benchmark()` to dispatch to it
3. Update `benchmark/__init__.py`'s `__all__` list

## ✅ Prediction File Validation

```bash
python -m benchmark.validate swe_predictions_open-agent-qwen-35b.jsonl
```

## 📝 Reporting Results

```yaml
Model: open-agent (v2.2)
Backend: llama.cpp
Agent Steps: 500 max
Mode: coding (full ReAct loop with search + tool use)

Benchmark: SWE-bench Lite (test split, n=300)
Evaluator: swebench.harness (Docker, v4.1.0)
Pass@1: XX.X%

Benchmark: BigCodeBench (instruct split, n=1140)
Evaluator: Local unittest
Pass@1: XX.X%
```

## 🐳 Docker Requirements

SWE-bench evaluation requires Docker:
```bash
sudo apt-get install docker.io
sudo usermod -aG docker $USER
newgrp docker
docker info
```
