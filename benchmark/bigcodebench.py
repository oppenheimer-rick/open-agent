"""
BigCodeBench — Code Synthesis Benchmark (open-agent native)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Used by Qwen, DeepSeek, and others for code synthesis evaluation.
Each problem has embedded unittest test cases — no external package needed.

Runs INSIDE open-agent's own loop: imports run_agent() directly from loop.py.

Usage:
  python -m benchmark.bigcodebench --instances 10
  python -m benchmark.bigcodebench --instances 50 --subset hard

From loop.py REPL:
  /benchmark bigcodebench --instances 10
  /benchmark bigcodebench --instances 50 --subset hard
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ── Imports (lazy) ────────────────────────────────────────────────────────────

try:
    from datasets import load_dataset  # type: ignore[import-untyped]
except ImportError:
    load_dataset = None  # type: ignore[assignment]


# ── Config ─────────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_INSTANCES: int | None = 10
OUTPUT_DIR = HERE / "reports"
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Load Problems ─────────────────────────────────────────────────────────────


def load_problems(
    max_instances: int | None = None,
    subset: str | None = None,
) -> list[dict]:
    """Load BigCodeBench problems from HuggingFace datasets."""
    if load_dataset is None:
        print("  ✗ datasets not installed. Run: pip install datasets")
        return []

    print("  Loading BigCodeBench (v0.1.4) from HuggingFace...")
    ds = load_dataset("bigcode/bigcodebench", split="v0.1.4", streaming=True)

    items: list[dict] = []
    for item in ds:
        items.append(dict(item))

    print(f"  Available: {len(items)} problems")

    if subset == "hard":
        items = [it for it in items if _is_hard(it)]
        print(f"  Hard subset: {len(items)} problems")

    if max_instances is not None and max_instances < len(items):
        items = items[:max_instances]

    print(f"  Processing: {len(items)} problems")
    return items


def _is_hard(item: dict) -> bool:
    ds = item.get("doc_struct", {})
    if isinstance(ds, str):
        return any(kw in ds for kw in ("Hard", "hard", "3"))
    if isinstance(ds, dict):
        return any(kw in str(ds.get("description", "")) for kw in ("Hard", "hard", "3"))
    return False


# ── Solution Extraction ───────────────────────────────────────────────────────


def extract_solution(agent_output: str, entry_point: str | None = None) -> str:
    """Extract generated code from the agent's final output."""
    if not agent_output:
        return ""

    # Strategy 1: Python code blocks
    blocks = re.findall(r"```python\n(.*?)\n```", agent_output, re.DOTALL)
    if blocks:
        return "\n\n".join(blocks)

    # Strategy 2: Generic code blocks
    blocks = re.findall(r"```\n(.*?)\n```", agent_output, re.DOTALL)
    if blocks:
        return "\n\n".join(blocks)

    # Strategy 3: Specific entry-point function
    if entry_point:
        match = re.search(
            rf"(def {re.escape(entry_point)}.*?)(?:\n(?!\s|\"\"\"|#))",
            agent_output,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()

    # Strategy 4: Any function/class definitions
    defns = re.findall(
        r"(?:def |class |import |from ).*?(?:\n(?!\s*\n).{0,500})*",
        agent_output,
        re.DOTALL,
    )
    if defns:
        return "\n\n".join(defns[:5])

    return agent_output.strip()[:1000]


# ── Local Test Evaluation ─────────────────────────────────────────────────────


def evaluate_with_tests(
    solution: str,
    test_code: str,
    entry_point: str | None = None,
    timeout: int = 10,
) -> dict:
    """
    Run the solution against unittest test cases.

    Returns: {"passed": bool, "num_tests": int, "num_passed": int, "errors": list}
    """
    result = {"passed": False, "num_tests": 0, "num_passed": 0, "errors": []}

    if not solution.strip():
        result["errors"].append("Empty solution")
        return result

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test_eval.py"
        combined = (
            solution.strip()
            + "\n\n# ── Tests ──\n"
            + test_code
            + "\n\nif __name__ == '__main__':\n    import unittest\n    unittest.main(verbosity=0, exit=False)\n"
        )
        test_file.write_text(combined)

        try:
            proc = subprocess.run(
                [sys.executable, str(test_file)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            result["errors"].append(f"Test timed out ({timeout}s)")
            return result
        except Exception as e:
            result["errors"].append(f"Execution error: {e}")
            return result

        output = (proc.stdout or "") + (proc.stderr or "")
        result["output"] = output[:500]

        ran = re.search(r"Ran (\d+) tests?", output)
        num_tests = int(ran.group(1)) if ran else 0
        fail = re.search(r"FAILED.*failures=(\d+)", output)
        failures = int(fail.group(1)) if fail else 0
        err = re.search(r"FAILED.*errors=(\d+)", output)
        errors_count = int(err.group(1)) if err else 0
        num_passed = num_tests - failures - errors_count
        passed = "OK" in output or (
            num_tests > 0 and failures == 0 and errors_count == 0
        )

        result["passed"] = passed
        result["num_tests"] = num_tests
        result["num_passed"] = num_passed

        if not passed:
            for line in output.split("\n"):
                if any(kw in line for kw in ("Error", "AssertionError", "Traceback")):
                    result["errors"].append(line.strip()[:150])
            result["errors"] = result["errors"][:3]

    return result


# ── Solve One Problem via run_agent() ─────────────────────────────────────────


def solve_problem(
    prompt: str,
    task_id: str,
    entry_point: str | None = None,
    max_steps: int = 100,
    quiet: bool = False,
) -> dict:
    """
    Solve one BigCodeBench problem by calling run_agent() directly.

    This is the KEY function — runs inside open-agent's own ReAct loop.
    """
    from loop import run_agent  # type: ignore[import-untyped]

    agent_prompt = (
        f"BIGCODEBENCH TASK: {task_id}\n\n"
        f"{prompt}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Write a complete Python solution.\n"
        f"{'2. The main function MUST be named ' + entry_point + '.' if entry_point else ''}\n"
        f"3. Include all necessary imports.\n"
        f"4. Output your final code in a ```python block.\n"
        f"5. Do NOT include test cases or example usage in your final output."
    )

    output_buffer = io.StringIO()
    with (
        contextlib.redirect_stdout(output_buffer) if quiet else contextlib.nullcontext()
    ):
        try:
            start = time.time()
            final_msg = run_agent(
                agent_prompt,
                mode="coding",
                max_steps=max_steps,
                skip_preflight=True,
            )
            duration = time.time() - start
        except Exception as e:
            duration = 0.0
            final_msg = f"Agent error: {e}"

    captured = output_buffer.getvalue() if quiet else ""
    solution = extract_solution(final_msg or captured, entry_point)

    return {
        "task_id": task_id,
        "solution": solution,
        "duration_s": round(duration, 1),
        "success": bool(solution),
    }


# ── Full Benchmark Runner ─────────────────────────────────────────────────────


def run_benchmark(
    max_instances: int | None = None,
    subset: str | None = None,
    save_path: str | None = None,
) -> dict:
    """
    Run the full BigCodeBench benchmark through open-agent's own loop.

    Args:
        max_instances: Number of problems to run (None = config default)
        subset: "hard" for hard problems only, None for all
        save_path: Path for incremental predictions (auto if None)

    Returns summary dict with pass@1 score.
    """
    # Config
    if max_instances is None:
        max_instances = DEFAULT_INSTANCES

    if save_path is None:
        save_path = str(
            OUTPUT_DIR / f"bigcodebench_predictions_{int(time.time())}.jsonl"
        )

    # Header
    print()
    print("  ╔════════════════════════════════════════════════════╗")
    print("  ║     BigCodeBench — open-agent Native Evaluation   ║")
    print("  ║     (inside agent's own ReAct loop)               ║")
    print("  ╚════════════════════════════════════════════════════╝")
    print("  Model:      open-agent  (loop.py)")
    print(f"  Subset:     {subset or 'all'}")
    print(f"  Max:        {max_instances} instances")
    print()

    # Load
    problems = load_problems(max_instances, subset)
    if not problems:
        print("  ✗ No problems loaded.")
        return {}

    # Resume support
    completed_ids: set[str] = set()
    if Path(save_path).exists():
        try:
            with open(save_path) as f:
                for line in f:
                    if line.strip():
                        completed_ids.add(json.loads(line).get("task_id", ""))
            print(f"  Already completed: {len(completed_ids)}")
        except (json.JSONDecodeError, KeyError):
            pass

    # Run
    results = []
    passed = 0
    total = 0

    for idx, prob in enumerate(problems):
        task_id = prob["task_id"]
        if task_id in completed_ids:
            print(f"  [{idx + 1}/{len(problems)}] ⏭  {task_id} — already done")
            continue

        total += 1
        prompt = prob.get("instruct_prompt") or prob.get("complete_prompt", "")
        entry_point = prob.get("entry_point", "")
        libs = prob.get("libs", [])
        test_code = prob.get("test", "")

        short = prompt[:80].replace("\n", " ")
        print(
            f"\n  [{idx + 1}/{len(problems)}] 🚀 {task_id}  (libs: {', '.join(libs[:3])})"
        )
        print(f"       {short}...")

        # Solve using open-agent's own loop
        result = solve_problem(prompt, task_id, entry_point)
        solution = result["solution"]
        duration = result["duration_s"]

        # Evaluate
        if solution and test_code:
            eval_result = evaluate_with_tests(solution, test_code, entry_point)
            passed_flag = eval_result["passed"]
        else:
            eval_result = {
                "passed": False,
                "num_tests": 0,
                "num_passed": 0,
                "errors": [],
            }
            passed_flag = bool(solution)

        if passed_flag:
            passed += 1
            print(
                f"    ✓ PASS  ({eval_result['num_passed']}/{eval_result['num_tests']} tests)  [{duration}s]"
            )
        else:
            print(
                f"    ✗ FAIL  ({eval_result['num_passed']}/{eval_result['num_tests']} tests)  [{duration}s]"
            )
            if eval_result["errors"]:
                for err in eval_result["errors"][:2]:
                    print(f"      • {err[:120]}")

        # Save incrementally
        record = {
            "task_id": task_id,
            "solution": solution,
            "entry_point": entry_point,
            "libs": libs,
            "duration_s": duration,
            "solution_len": len(solution),
            "eval": eval_result,
        }
        results.append(record)

        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "a") as f:
            f.write(json.dumps(record, indent=None) + "\n")

        # Live rate
        if total > 0:
            rate = (passed / total) * 100
            print(f"    ── Running pass@1: {rate:.1f}% ({passed}/{total})")

    # Summary
    rate = (passed / total * 100) if total > 0 else 0
    print()
    print("  " + "═" * 45)
    print("  BIGCODEBENCH RESULTS")
    print("  " + "═" * 45)
    print(f"  Problems:   {total}")
    print(f"  Passed:     {passed}")
    print(f"  Pass@1:     {rate:.1f}%")
    print(f"  Saved to:   {save_path}")
    print()

    summary = {
        "benchmark": "bigcodebench",
        "model": "open-agent",
        "subset": subset or "all",
        "problems": total,
        "passed": passed,
        "pass_at_1": round(rate, 1),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "save_path": save_path,
    }

    # Save summary
    summary_path = OUTPUT_DIR / "bigcodebench_results.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="BigCodeBench — open-agent native code synthesis evaluation",
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=DEFAULT_INSTANCES,
        help="Number of problems to solve",
    )
    parser.add_argument(
        "--subset",
        choices=["hard", "complete"],
        default=None,
        help="Run only hard problems or complete set",
    )
    args = parser.parse_args()

    run_benchmark(
        max_instances=args.instances,
        subset=args.subset,
    )
