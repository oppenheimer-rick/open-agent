"""
Agentic Tool-Use Benchmark — runs INSIDE open-agent's own loop
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tests the agent's core capabilities: file reading, code editing, bash execution,
Python scripting, web search, data processing, and debugging.

Each task is deterministic — setup creates controlled files, the agent solves
the problem using its full toolkit, and verification checks the result.

Designed after the SWE-bench agentic evaluation methodology but for
general tool-use capability rather than software engineering only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ── Task Definitions ──────────────────────────────────────────────────────────

# New tasks defined below via assign to fresh variable
_AGENTIC_TASKS: list[dict] = [
    # ── Task 1: Build an OpenAI-compatible API proxy for a local LLM ──
    {
        "id": "llm_api_proxy",
        "description": "Build an OpenAI-compatible API proxy wrapping llama.cpp server",
        "prompt": (
            "We run a local llama.cpp server on http://localhost:8083 that exposes a raw completion endpoint. "
            "I need you to build a Python proxy server using FastAPI that translates OpenAI Chat Completions API calls "
            "into llama.cpp server requests and back.\n\n"
            "Requirements:\n"
            "1. Create 'proxy.py' — a FastAPI app with a single route POST /v1/chat/completions\n"
            "2. Accept OpenAI-format requests (model, messages array with role/content, temperature, max_tokens)\n"
            "3. Convert messages to a single prompt string using the format: '<|im_start|>role\\ncontent<|im_end|>'"
            '   then call POST http://localhost:8083/completion with \'{"prompt": ..., "temperature": ..., "n_predict": ...}\'\n'
            '4. Parse the llama.cpp response and return it in OpenAI format: \'{"choices": [{"message": {"role": "assistant", "content": ...}}]}\'\n'
            "5. Add proper error handling — if llama.cpp returns an error, return a 502 with details\n"
            "6. Include a --dry-run mode (python proxy.py --dry-run) that prints the transformed request without calling the server\n"
            "7. Use httpx for async HTTP calls to the backend"
        ),
        "setup": {},
        "verify": (
            'python3 -c "'
            "from proxy import app; "
            "routes = [r.path for r in app.routes]; "
            "assert '/v1/chat/completions' in routes or any('/v1/chat/completions' in str(r) for r in routes); "
            "print('proxy.py has the /v1/chat/completions route')"
            '"'
        ),
    },
    # ── Task 2: Build a prompt template optimizer ──
    {
        "id": "prompt_optimizer",
        "description": "Build a CLI tool that optimises system prompts for local LLMs",
        "prompt": (
            "We need a tool called 'prompt_optimizer.py' that takes a system prompt and optimises it "
            "for local LLM inference (specifically llama.cpp). The tool should:\n\n"
            "1. Read a system prompt from stdin or a file (--file argument)\n"
            "2. Apply a series of optimisations:\n"
            "   a. Strip trailing whitespace and normalise line endings\n"
            "   b. Replace markdown tables with concise bullet-point lists (models with limited context)\n"
            "   c. Remove redundant instructions (e.g., 'you are an AI assistant' when implicit)\n"
            "   d. Compress multi-line examples into compact one-line formats where possible\n"
            "   e. Add 'IMPORTANT: Keep responses concise' at the end\n"
            "3. Output the optimised prompt to stdout\n"
            "4. Support a --stats flag that prints: original_tokens, optimised_tokens, compression_ratio\n"
            "5. Token counting should estimate at ~4 characters per token"
        ),
        "setup": {},
        "verify": (
            'python3 -c "'
            "from prompt_optimizer import optimize_prompt; "
            "original = 'You are a helpful AI assistant. You help with coding. ' * 20; "
            "result = optimize_prompt(original); "
            "assert len(result) < len(original); "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 3: Build a model selection router ──
    {
        "id": "model_router",
        "description": "Build a smart model router that selects the right local model per task",
        "prompt": (
            "We have three local models running: Qwen2.5-7B (http://localhost:8081), "
            "Llama-3.2-3B (http://localhost:8082), and DeepSeek-Coder-V2-Lite (http://localhost:8083). "
            "Each has different strengths. Build 'router.py' that:\n\n"
            "1. Defines a 'RouteConfig' dataclass per model: name, endpoint, strengths (list of task keywords)\n"
            "2. A function 'classify_task(prompt: str) -> str' that returns one of: 'coding', 'reasoning', 'creative', 'simple'\n"
            "   based on keyword matching and prompt length heuristics\n"
            "3. A function 'route(prompt: str, task_type: str | None = None) -> dict' that:\n"
            "   - Auto-classifies if task_type is None\n"
            "   - Selects the best model: Qwen for reasoning, DeepSeek for coding, Llama for creative/simple\n"
            '   - Returns {"model": ..., "endpoint": ..., "task_type": ..., "confidence": ...}\n'
            "4. A CLI mode: 'python router.py --prompt \"write a sort function\"' that prints the route decision\n"
            "5. Include proper type hints and a docstring for every public function"
        ),
        "setup": {},
        "verify": (
            'python3 -c "'
            "from router import route, classify_task; "
            "assert classify_task('write a bubble sort in python') == 'coding'; "
            "r = route('what is the meaning of life?'); "
            "assert 'model' in r and 'endpoint' in r; "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 4: Parse and visualise llama.cpp server metrics from logs ──
    {
        "id": "log_analyzer",
        "description": "Build a log analyser for llama.cpp server metrics from log files",
        "prompt": (
            "There is a file 'server.log' containing llama.cpp server output with timestamps "
            "and performance metrics. Build 'log_analyzer.py' that:\n\n"
            "1. Parses the log file looking for lines containing 'llama_print_timings' or 'model_load'\n"
            "2. Extracts these metrics per request:\n"
            "   - prompt_tokens (e.g., 'prompt tokens =    1234')\n"
            "   - generated_tokens (e.g., 'generated tokens =     567')\n"
            "   - timing (e.g., 'total time = 12345.67 ms')\n"
            "3. Computes aggregate statistics:\n"
            "   - Total requests\n"
            "   - Mean tokens per second\n"
            "   - Mean prompt processing rate (tokens/s)\n"
            '4. Writes a summary to \'log_summary.json\': {"requests": N, "avg_tokens_per_sec": X, "avg_prompt_rate": Y}\n'
            "5. The log file format has lines like:\n"
            "   'llama_print_timings: prompt tokens = 1234 (  123.45 ms per token)'\n"
            "6. Use regex patterns, handle missing values gracefully"
        ),
        "setup": {
            "server.log": (
                "main: build = 1234\n"
                "main: seed = 42\n"
                "llama_model_load: model loaded\n"
                "llama_print_timings: prompt tokens =   1234 (  123.45 ms per token)\n"
                "llama_print_timings: generated tokens =   4567 (   45.67 ms per token)\n"
                "llama_print_timings: total time = 56789.01 ms\n"
                "llama_print_timings: prompt tokens =    567 (   89.12 ms per token)\n"
                "llama_print_timings: generated tokens =   1234 (   12.34 ms per token)\n"
                "llama_print_timings: total time = 12345.67 ms\n"
            )
        },
        "verify": (
            'python3 -c "'
            "import json; "
            "data = json.load(open('log_summary.json')); "
            "assert data['requests'] >= 2; "
            "assert data['avg_tokens_per_sec'] > 0; "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 5: Build a system health monitor for inference servers ──
    {
        "id": "system_monitor",
        "description": "Build a GPU/memory monitor script for local LLM inference",
        "prompt": (
            "Build 'monitor.py' — a CLI system monitor for machines running local LLM inference.\n\n"
            "Requirements:\n"
            "1. A function 'get_gpu_info()' that runs 'nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader'\n"
            '   and returns a list of dicts: [{"memory_used_mb": ..., "memory_total_mb": ..., "gpu_util_pct": ...}]\n'
            '2. A function \'get_cpu_info()\' that reads /proc/meminfo and returns {"memory_used_mb": ..., "memory_total_mb": ...}\n'
            "3. A function 'get_load_avg()' that returns os.getloadavg() as a tuple\n"
            "4. A function 'format_dashboard()' that renders all metrics as a clean terminal dashboard:\n"
            "   ┌─────────────────────────────────────────────┐\n"
            "   │  GPU 0: 4.2 GB / 8.0 GB  (52%)  87% util  │\n"
            "   │  RAM:   12.5 GB / 31.2 GB (40%)            │\n"
            "   │  Load:  2.3, 1.8, 1.5                      │\n"
            "   └─────────────────────────────────────────────┘\n"
            "5. CLI: 'python monitor.py' runs the dashboard once and exits\n"
            "6. Handle the case where nvidia-smi is not available (return empty list, show CPU-only view)"
        ),
        "setup": {},
        "verify": (
            'python3 -c "'
            "from monitor import get_gpu_info, get_cpu_info, get_load_avg, format_dashboard; "
            "cpu = get_cpu_info(); "
            "assert 'memory_used_mb' in cpu; "
            "assert 'memory_total_mb' in cpu; "
            "dashboard = format_dashboard(); "
            "assert 'GPU' in dashboard or 'RAM' in dashboard or 'Load' in dashboard; "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 6: Build a quantisation config generator ──
    {
        "id": "quant_config_generator",
        "description": "Generate quantisation config files for llama.cpp model conversions",
        "prompt": (
            "We need a tool 'quant_gen.py' that generates llama.cpp quantisation configuration files "
            "for model conversion.\n\n"
            "Requirements:\n"
            "1. Define quantisation presets as a dict:\n"
            '   presets = {"q4_k_m": {"type": "q4_k_m", "quality": "good", '
            '"size_gb_per_7b": 4.1, "speed": "fast"},\n'
            '   "q5_k_m": {"type": "q5_k_m", "quality": "better", '
            '"size_gb_per_7b": 5.1, "speed": "moderate"},\n'
            '   "q8_0": {"type": "q8_0", "quality": "best", '
            '"size_gb_per_7b": 7.5, "speed": "slow"}}\n'
            "2. A function 'recommend_quant(model_size_b: float, vram_gb: float) -> str' that:\n"
            "   - Calculates how much space each quant would take (model_size_b * size_gb_per_7b / 7.0)\n"
            "   - Returns the best quant that fits in vram_gb with the best quality\n"
            "3. A function 'generate_config(model_name: str, quant: str, output_path: str)' that writes a JSON config:\n"
            '   {"model": model_name, "quantization": quant, '
            '"n_gpu_layers": -1, "n_ctx": 4096, "temperature": 0.7}\n'
            "4. CLI mode: 'python quant_gen.py --model llama-7b --vram 6.0' prints the recommendation\n"
            "5. CLI mode: 'python quant_gen.py --model llama-7b --quant q4_k_m --output config.json' "
            "writes the config file"
        ),
        "setup": {},
        "verify": (
            'python3 -c "'
            "from quant_gen import recommend_quant, generate_config; "
            "rec = recommend_quant(7.0, 6.0); "
            "assert rec in ['q4_k_m', 'q5_k_m', 'q8_0']; "
            "generate_config('llama-7b', 'q4_k_m', '/tmp/test_config.json'); "
            "import json, os; "
            "cfg = json.load(open('/tmp/test_config.json')); "
            "assert cfg['model'] == 'llama-7b'; "
            "os.unlink('/tmp/test_config.json'); "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 7: Debug a broken data pipeline with multiple issues ──
    {
        "id": "debug_pipeline",
        "description": "Debug a broken data pipeline with multiple issues",
        "prompt": (
            "There are two files with bugs: 'pipeline.py' and 'utils.py'. The pipeline reads a CSV, "
            "processes it, and writes results. It's failing silently — producing an empty output.\n\n"
            "Your job:\n"
            "1. Read both files to understand the flow\n"
            "2. Run the pipeline and observe what happens\n"
            "3. Fix ALL the bugs you find (there are at least 3)\n"
            "4. Verify the pipeline produces correct output\n"
            "5. Add a simple progress bar using tqdm or a manual implementation"
        ),
        "setup": {
            "pipeline.py": (
                "import csv\n"
                "from utils import validate_row, transform_row\n\n"
                "def run_pipeline(input_path, output_path):\n"
                "    with open(input_path) as f:\n"
                "        reader = csv.DictReader(f)\n"
                "        with open(output_path, 'w') as out:\n"
                "            writer = csv.DictWriter(out, fieldnames=['name', 'score', 'grade'])\n"
                "            for row in reader:\n"
                "                if not validate_row(row):\n"
                "                    continue\n"
                "                transformed = transform_row(row)\n"
                "                writer.writerow(row)  # BUG: should write transformed, not row\n"
                "if __name__ == '__main__':\n"
                "    run_pipeline('input.csv', 'output.csv')\n"
            ),
            "utils.py": (
                "def validate_row(row):\n"
                "    # BUG: always returns False because row has string fields\n"
                "    if row['score'] > 100:\n"
                "        return False\n"
                "    return True\n\n"
                "def transform_row(row):\n"
                "    score = int(row['score'])\n"
                "    if score >= 90:\n"
                "        grade = 'A'\n"
                "    elif score >= 80:\n"
                "        grade = 'B'\n"
                "    elif score >= 70:\n"
                "        grade = 'C'\n"
                "    else:\n"
                "        grade = 'F'  # BUG: below 70 should be D, not F\n"
                "    return {**row, 'grade': grade, 'score': score}\n"
            ),
            "input.csv": ("name,score\nAlice,95\nBob,82\nCharlie,65\n"),
        },
        "verify": (
            'python3 -c "'
            "from pipeline import run_pipeline; "
            "run_pipeline('input.csv', '/tmp/test_output.csv'); "
            "import csv; "
            "rows = list(csv.DictReader(open('/tmp/test_output.csv'))); "
            "assert len(rows) == 3; "
            "assert rows[0]['grade'] == 'A'; "
            "assert rows[2]['grade'] == 'D'; "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 8: Build a tiny evaluation harness ──
    {
        "id": "eval_harness",
        "description": "Build a mini evaluation harness that tests a model on a set of prompts",
        "prompt": (
            "Build 'eval_harness.py' — a minimal evaluation harness that tests a local LLM endpoint "
            "against a set of test cases.\n\n"
            "Requirements:\n"
            "1. A 'TestCase' dataclass with: prompt (str), expected_substring (str), max_tokens (int=128)\n"
            "2. A function 'load_testcases(path: str) -> list[TestCase]' that reads a JSON file:\n"
            '   [{"prompt": "...", "expected": "..."}]\n'
            "3. A function 'evaluate(endpoint: str, testcases: list[TestCase]) -> list[dict]' that:\n"
            "   - Sends each prompt to the endpoint via httpx POST\n"
            "   - Checks if expected_substring is in the response (case-insensitive)\n"
            '   - Returns [{"prompt": ..., "passed": bool, "response": ..., "latency_ms": ...}]\n'
            "4. A function 'report(results: list[dict]) -> dict' that returns:\n"
            '   {"total": N, "passed": N, "score": X.X, "avg_latency_ms": Y.Y}\n'
            "5. CLI: 'python eval_harness.py testcases.json --endpoint http://localhost:8083/completion'\n"
            "   prints results and saves 'eval_report.json'"
        ),
        "setup": {
            "testcases.json": json.dumps(
                [
                    {"prompt": "What is 2+2?", "expected": "4"},
                    {"prompt": "Capital of France", "expected": "Paris"},
                    {"prompt": "RGB color model", "expected": "red"},
                ]
            )
        },
        "verify": (
            'python3 -c "'
            "from eval_harness import load_testcases, TestCase; "
            "cases = load_testcases('testcases.json'); "
            "assert len(cases) == 3; "
            "assert isinstance(cases[0], TestCase); "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 9: Build a context window analyser ──
    {
        "id": "context_window_analyzer",
        "description": "Build a tool that analyses and visualises context window usage from prompts",
        "prompt": (
            "Build 'context_analyzer.py' — a tool that helps users understand how their prompts "
            "use the context window of local LLMs.\n\n"
            "Requirements:\n"
            "1. A function 'tokenize(text: str) -> list[str]' that splits text by whitespace and punctuation,"
            "   approximating ~4 chars per token (simple heuristic)\n"
            "2. A function 'analyze_prompt(prompt: str, system_prompt: str = \"\", max_context: int = 8192) -> dict' that returns:\n"
            '   {"system_tokens": N, "user_tokens": N, "total_tokens": N, '
            '"remaining_tokens": max_context - total,\n'
            '    "usage_pct": round(total / max_context * 100, 1), '
            '"sections": [{"origin": "system"|"user", "tokens": N, "text_preview": "..."}]}\n'
            "3. A function 'format_report(stats: dict) -> str' that renders a clean CLI output:\n"
            "   Context:  [████████░░░░░░] 45%  (3686 / 8192 tokens)\n"
            "   System:   [████████░░░░░░] 45%  (3686 tokens)\n"
            "   User:     [░░░░░░░░░░░░░░]  0%  (0 tokens)\n"
            "   Remaining: 4506 tokens\n"
            '4. CLI: \'python context_analyzer.py --prompt "tell me a story" --system "you are a poet"\'\n'
            "5. Support reading prompt from a file with --prompt-file"
        ),
        "setup": {},
        "verify": (
            'python3 -c "'
            "from context_analyzer import analyze_prompt, format_report, tokenize; "
            "stats = analyze_prompt('hello world', system_prompt='be helpful'); "
            "assert 'total_tokens' in stats; "
            "assert 'usage_pct' in stats; "
            "assert stats['total_tokens'] > 0; "
            "report = format_report(stats); "
            "assert 'Context' in report; "
            "assert 'System' in report; "
            "print('OK')"
            '"'
        ),
    },
    # ── Task 10: Build an agent skill generator ──
    {
        "id": "skill_generator",
        "description": "Build a tool that generates reusable agent skill files from examples",
        "prompt": (
            "Build 'skill_gen.py' — a tool that generates reusable agent skill files "
            "from example task descriptions. The agent uses SKILL.md files to define "
            "reusable capabilities.\n\n"
            "Requirements:\n"
            "1. A 'Skill' dataclass: name (str), description (str), triggers (list[str]), "
            "steps (list[str]), example (str)\n"
            "2. A function 'generate_skill(name: str, description: str, triggers: list[str], "
            "steps: list[str], example: str) -> str' that returns a formatted SKILL.md:\n"
            "   # <name>\n"
            "   \n"
            "   <description>\n"
            "   \n"
            "   ## Triggers\n"
            "   - <trigger1>\n"
            "   - <trigger2>\n"
            "   \n"
            "   ## Steps\n"
            "   1. <step1>\n"
            "   2. <step2>\n"
            "   \n"
            "   ## Example\n"
            "   ```\n"
            "   <example>\n"
            "   ```\n"
            "3. A function 'save_skill(content: str, output_dir: str = \".\")' that writes to "
            "output_dir/<name_lowercase>/SKILL.md\n"
            "4. A function 'batch_generate(specs: list[dict])' that takes a list of skill specs and "
            "generates all of them in one call\n"
            "5. CLI modes:\n"
            '   - \'python skill_gen.py --name code-reviewer --desc "Reviews Python code" '
            '--triggers "review,audit" --steps "1. Read file" --example "Input: ..."\'\n'
            "   - 'python skill_gen.py --batch specs.json' (JSON file with list of skill specs)\n"
            "6. Include proper error handling: validate that name has no spaces, triggers is not empty"
        ),
        "setup": {},
        "verify": (
            'python3 -c "'
            "from skill_gen import generate_skill, Skill; "
            "content = generate_skill('test-skill', 'A test', ['test'], ['Step 1'], 'example'); "
            "assert '# test-skill' in content; "
            "assert 'A test' in content; "
            "assert '## Steps' in content; "
            "s = Skill('test', 'desc', ['t1'], ['s1'], 'ex'); "
            "assert s.name == 'test'; "
            "print('OK')"
            '"'
        ),
    },
]

TASKS = _AGENTIC_TASKS


def run_agent_internal(prompt: str, work_dir: Path, max_steps: int = 100) -> str:
    """Run loop.py as subprocess inside work_dir. Returns final message."""
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent.parent / "loop.py"),
        prompt,
        "--coding",
        "--steps",
        str(max_steps),
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(work_dir),
            timeout=180,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        output = "TIMEOUT"
    except Exception as e:
        output = f"ERROR: {e}"

    return output


def run_task_via_agent(task: dict) -> dict:
    """Run a single tool-use task."""
    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)

        # Write setup files
        for filename, content in task["setup"].items():
            (work_dir / filename).write_text(content)

        # Run the agent
        start = time.time()
        run_agent_internal(task["prompt"], work_dir)
        duration = time.time() - start

        # Verify
        verify_cmd = task.get("verify", "")
        passed = False

        if verify_cmd:
            try:
                r = subprocess.run(
                    verify_cmd,
                    cwd=str(work_dir),
                    shell=True,
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                passed = r.returncode == 0
            except subprocess.TimeoutExpired:
                passed = False
            except Exception:
                passed = False

        return {
            "id": task["id"],
            "passed": passed,
            "duration_s": round(duration, 1),
        }


def run_agentic_bench(max_instances: int | None = None) -> dict:
    """Run the full agentic tool-use benchmark."""
    tasks = TASKS[:max_instances] if max_instances else TASKS

    print("╔═══════════════════════════════════════════════════╗")
    print("║    Agentic Tool-Use Benchmark (open-agent)       ║")
    print("║    Tests file ops · bash · python · search       ║")
    print("╚═══════════════════════════════════════════════════╝")
    print(f"  Tasks: {len(tasks)}")
    print()

    results = []
    passed = 0
    total = 0

    for task in tasks:
        total += 1
        print(f"\n  [{total}/{len(tasks)}] 🛠️  {task['id']}")
        print(f"       {task['description']}")

        result = run_task_via_agent(task)

        if result["passed"]:
            passed += 1
            print(f"    ✓ PASS  ({result['duration_s']}s)")
        else:
            print(f"    ✗ FAIL  ({result['duration_s']}s)")

        results.append(result)

        if total > 0:
            rate = (passed / total) * 100
            print(f"    ── Running: {rate:.1f}% ({passed}/{total})")

    # Summary
    rate = (passed / total * 100) if total > 0 else 0
    print()
    print("  " + "═" * 45)
    print("  AGENTIC TOOL-USE BENCHMARK RESULTS")
    print("  " + "═" * 45)
    print(f"  Tasks:     {total}")
    print(f"  Passed:    {passed}")
    print(f"  Score:     {rate:.1f}%")
    print()

    summary = {
        "benchmark": "agentic-bench",
        "tasks": total,
        "passed": passed,
        "score": round(rate, 1),
    }

    # Save results
    save_path = (
        Path(__file__).resolve().parent / "reports" / "agentic_bench_results.json"
    )
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    run_agentic_bench()
