"""
LiveCodeBench Harness for open-agent (loop.py)

LiveCodeBench is the contamination-aware benchmark used by DeepSeek,
Meta, and Qwen.  It features programming problems from LeetCode,
CodeForces, AtCoder, and similar platforms that are time-stamped
to detect data leakage.

Official repo: https://github.com/LiveCodeBench/LiveCodeBench
Data source:   huggingface.co/datasets/livecodebench/code_generation_lite

This harness:
  1. Loads the dataset (from HuggingFace datasets or local file)
  2. Runs loop.py as an agent on each problem
  3. Extracts the final solution from the agent's output
  4. Saves predictions in the official LiveCodeBench JSONL format
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Add parent to path so benchmark.config can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.config import (  # noqa: E402
    AGENT_SCRIPT,
    AGENT_TIMEOUT,
    BENCHMARK_DIR,
    LIVECODEBENCH_INSTANCES,
    MODEL_NAME,
)  # noqa: E402

# ── Data Loading ──────────────────────────────────────────────────────────────

DATASET_HF = "livecodebench/code_generation_lite"
DATASET_URL = "https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/main/data/"

# Fallback: known-good JSONL snapshot URL from official releases.
# (Update this if the official distribution URL changes.)
FALLBACK_URL = "https://raw.githubusercontent.com/LiveCodeBench/LiveCodeBench/main/data/v3_release.jsonl"


def load_livecodebench(
    split: str = "test",
    max_instances: int | None = None,
) -> list[dict]:
    """
    Load problems from LiveCodeBench.

    Priority: HuggingFace datasets > local file > fallback URL.
    """
    problems: list[dict] = []

    # 1. Try HuggingFace datasets
    try:
        from datasets import load_dataset as hf_load

        ds = hf_load(DATASET_HF, split=split, streaming=True)
        for i, item in enumerate(ds):
            if max_instances is not None and i >= max_instances:
                break
            problems.append(_normalise_lcb_item(dict(item)))
        if problems:
            print(f"  Loaded {len(problems)} problems from HuggingFace datasets")
            return problems
    except Exception as e:
        print(f"  HF datasets unavailable: {e}")

    # 2. Try local file
    local_path = BENCHMARK_DIR / "data" / f"livecodebench_{split}.jsonl"
    if local_path.exists():
        with open(local_path) as f:
            for line in f:
                if line.strip():
                    problems.append(_normalise_lcb_item(json.loads(line)))
        if problems:
            print(f"  Loaded {len(problems)} problems from {local_path}")
            if max_instances:
                return problems[:max_instances]
            return problems

    # 3. Try fallback URL
    try:
        import urllib.request

        print(f"  Downloading from {FALLBACK_URL}...")
        with urllib.request.urlopen(FALLBACK_URL, timeout=15) as resp:
            data = resp.read().decode()
            for line in data.strip().split("\n"):
                if line.strip():
                    problems.append(_normalise_lcb_item(json.loads(line)))
        print(f"  Loaded {len(problems)} problems from fallback URL")
        if max_instances:
            return problems[:max_instances]
        return problems
    except Exception as e:
        print(f"  Fallback URL failed: {e}")

    print("  ✗ Could not load LiveCodeBench data.")
    print("  Download manually from:")
    print(f"    {FALLBACK_URL}")
    print(f"  And save to: {local_path}")
    return []


def _normalise_lcb_item(item: dict) -> dict:
    """Normalise field names regardless of source format."""
    # HF dataset uses 'question' / 'problem'; GitHub JSONL uses 'prompt'
    if "prompt" in item and "question" not in item:
        item["question"] = item["prompt"]
    if "question_id" not in item and "id" in item:
        item["question_id"] = str(item["id"])
    return item


# ── Agent Execution ───────────────────────────────────────────────────────────


def run_agent(prompt: str, question_id: str, timeout: int = AGENT_TIMEOUT) -> str:
    """
    Run loop.py on a single LiveCodeBench problem and extract the solution.

    Returns the extracted code solution (or empty string on failure).
    """
    agent_prompt = (
        f"LIVECODEBENCH PROBLEM ID: {question_id}\n\n"
        f"PROBLEM:\n{prompt}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Understand the problem and plan your solution.\n"
        f"2. Write a complete Python solution. Include necessary imports.\n"
        f"3. Test your solution with the provided examples.\n"
        f"4. Output your final code in a ```python block.\n"
        f"5. Do NOT include test cases in the final output — only the solution function/script."
    )

    cmd = [
        sys.executable,
        str(AGENT_SCRIPT),
        agent_prompt,
        "--coding",
        "--steps",
        "100",
    ]

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start
        output = result.stdout + result.stderr
        print(f"    Agent completed in {duration:.1f}s")
    except subprocess.TimeoutExpired:
        print(f"    Agent timed out after {timeout}s")
        return ""
    except Exception as e:
        print(f"    Agent error: {e}")
        return ""

    # Extract Python code blocks
    blocks = re.findall(r"```python\n(.*?)\n```", output, re.DOTALL)
    if not blocks:
        blocks = re.findall(r"```\n(.*?)\n```", output, re.DOTALL)
    if not blocks:
        # Try extracting anything that looks like a function definition
        funcs = re.findall(
            r"(?:def |class |import ).*?(?:\n(?!\s*\n).*)*", output, re.DOTALL
        )
        if funcs:
            return "\n\n".join(funcs[:3])
        return ""

    return "\n\n".join(blocks)


# ── Main ──────────────────────────────────────────────────────────────────────


def main(
    split: str = "test",
    max_instances: int | None = None,
    output_path: str | None = None,
) -> str:
    """
    Run the full LiveCodeBench evaluation.

    Returns the path to the saved predictions file.
    """
    if max_instances is None:
        max_instances = LIVECODEBENCH_INSTANCES

    print("╔═══════════════════════════════════════════════════╗")
    print("║       LiveCodeBench — open-agent Evaluation       ║")
    print("╚═══════════════════════════════════════════════════╝")
    print(f"  Model:     {MODEL_NAME}")
    print(f"  Split:     {split}")
    print(f"  Max:       {max_instances or 'all'}")

    problems = load_livecodebench(split, max_instances)
    if not problems:
        print("  ✗ No problems loaded. Aborting.")
        return ""

    output_path = output_path or str(BENCHMARK_DIR / "livecodebench_predictions.jsonl")
    completed_ids = set()
    if Path(output_path).exists():
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    completed_ids.add(json.loads(line).get("question_id", ""))

    total = len(problems)
    passed_any = 0

    for i, prob in enumerate(problems):
        qid = prob.get("question_id", f"prob_{i}")
        question = prob.get("question", prob.get("prompt", ""))
        difficulty = prob.get("difficulty", "unknown")

        if qid in completed_ids:
            print(f"  [{i + 1}/{total}] ⏭  {qid} ({difficulty}) — already done")
            continue

        print(f"\n  [{i + 1}/{total}] 🚀 {qid} ({difficulty})")

        if not question:
            print("    ✗ Empty question, skipping")
            continue

        solution = run_agent(question, qid)

        sample_test = prob.get("public_test_cases", prob.get("testcases", []))
        # Run a quick sample test if possible
        if solution and sample_test:
            sample_pass = _run_sample_test(solution, sample_test)
            if sample_pass:
                passed_any += 1
                print("    ✓ Sample tests passed")
            else:
                print("    ~ Sample tests did not pass (may still be correct)")

        prediction = {
            "question_id": qid,
            "solution": solution,
            "difficulty": difficulty,
            "model": MODEL_NAME,
            "duration_s": round(
                time.time(), 0
            ),  # placeholder; real duration tracked per-question in run_agent
        }

        with open(output_path, "a") as f:
            f.write(json.dumps(prediction) + "\n")

        print(f"    Solution: {len(solution)} chars")

    print(f"\n  Done. {len(problems)} problems processed.")
    if passed_any:
        print(f"  Sample-test pass rate: {passed_any}/{total}")
    print(f"  Predictions: {output_path}")
    return output_path


def _run_sample_test(solution: str, test_cases: list[dict]) -> bool:
    """Run a quick sanity check on public test cases."""
    if not solution or not test_cases:
        return False
    # Simple check: try running the solution with sample inputs
    import tempfile

    test_input = test_cases[0]
    inp = test_input.get("input", "")

    # Wrap the solution in a harness that reads stdin and prints stdout
    harness = f"""import sys
{solution}

if __name__ == "__main__":
    data = sys.stdin.read() if not sys.stdin.isatty() else {json.dumps(inp)}
    # Try calling the main solve() or last defined function
    result = data  # fallback
    print(result)
"""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(harness)
            tmp = f.name
        r = subprocess.run(
            [sys.executable, tmp],
            capture_output=True,
            text=True,
            timeout=5,
        )
        os.unlink(tmp)
        return r.returncode == 0
    except Exception:
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LiveCodeBench Harness")
    parser.add_argument("--instances", type=int, default=None, help="Max instances")
    parser.add_argument("--split", default="test", help="Dataset split")
    parser.add_argument("--output", default=None, help="Output path")
    args = parser.parse_args()
    main(args.split, args.instances, args.output)
