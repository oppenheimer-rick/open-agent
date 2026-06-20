"""
Shared benchmark configuration for open-agent (loop.py).

Centralises model identity, paths, and run parameters so every harness
and the unified runner speak the same versioning language.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Model Identity ────────────────────────────────────────────────────────────
MODEL_NAME = os.environ.get("BENCHMARK_MODEL", "open-agent-qwen-35b")
MODEL_VERSION = "2.2"  # matches the open-agent build version

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
AGENT_SCRIPT = ROOT / "loop.py"
BENCHMARK_DIR = ROOT / "benchmark"
REPORT_DIR = BENCHMARK_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

# ── Agent Run Parameters ──────────────────────────────────────────────────────
MAX_STEPS = 500  # industry standard for complex agentic loops
INSTANCE_TIMEOUT = 1200  # 20 minutes per instance (SWE-bench standard)
AGENT_TIMEOUT = 180  # 3 minutes per BigCodeBench / LiveCodeBench task

# ── Default Instance Counts ───────────────────────────────────────────────────
# Set to None for full run; small defaults for smoke-testing.
SWEBENCH_INSTANCES: int | None = 10  # full set is 300
BIGCODEBENCH_INSTANCES: int | None = 10  # full set is 1140
LIVECODEBENCH_INSTANCES: int | None = 10  # full set varies

# ── SWE-bench ─────────────────────────────────────────────────────────────────
SWEBENCH_DATASET = "princeton-nlp/SWE-bench_Lite"
SWEBENCH_SPLIT = "test"

# ── BigCodeBench ──────────────────────────────────────────────────────────────
BIGCODEBENCH_DATASET = "bigcode/bigcodebench"
BIGCODEBENCH_SPLIT = "v0.1.4"  # latest version (1140 problems)

# ── LiveCodeBench ─────────────────────────────────────────────────────────────
LIVECODEBENCH_DATASET = "livecodebench/livecodebench"
LIVECODEBENCH_VERSION = "v3"  # latest contamination-aware release
LIVECODEBENCH_SPLIT = "test"
