"""
Open-Agent Benchmarks
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Each benchmark is a standalone module that imports run_agent() directly
from loop.py — running INSIDE the agent's own ReAct loop.

Benchmarks:
  benchmark.bigcodebench      Code synthesis (unittest evaluation, 1140 problems)
  benchmark.swebench          Software engineering (git patches + Docker eval)
  benchmark.agentic_bench     Agent tool-use benchmark (10 deterministic tasks)
  benchmark.gaia              Multi-step reasoning (requires HF auth)
  benchmark.livecodebench     Contamination-aware coding benchmark

Usage from loop.py REPL:
  /benchmark bigcodebench --instances 10
  /benchmark swebench --instances 5
  /benchmark agentic-bench
  /benchmark gaia --instances 5

Direct CLI:
  python -m benchmark.bigcodebench --instances 10
  python -m benchmark.swebench --instances 5 --evaluate
  python -m benchmark.agentic_bench
  python -m benchmark.gaia --instances 5

Dependencies per benchmark:
  bigcodebench   → datasets
  swebench       → datasets, GitPython, swebench, docker
  agentic_bench  → none (pure local, always works)
  gaia           → huggingface_hub, datasets
  livecodebench  → datasets
"""

from __future__ import annotations

__all__ = [
    "bigcodebench",
    "swebench",
    "agentic_bench",
    "gaia",
    "livecodebench",
]

__version__ = "2.2"
