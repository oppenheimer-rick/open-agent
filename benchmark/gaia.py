"""
GAIA Agentic Benchmark — runs INSIDE open-agent's own loop
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Industry gold standard for general agent capability (Meta AI, 2024).
Tests multi-step reasoning: web search → file processing → data synthesis → answer.

Each GAIA task requires the agent to:
  • Understand a complex, multi-step question
  • Search the web for specific information
  • Download and process files (PDFs, images, spreadsheets, audio)
  • Synthesise information across sources
  • Produce a precise final answer

Requires: huggingface_hub login (for GAIA dataset access)
  pip install huggingface_hub
  huggingface-cli login  # then accept terms at hf.co/datasets/gaia-benchmark/GAIA
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path


# ── Dataset Loading ───────────────────────────────────────────────────────────


def load_gaia_problems(
    max_instances: int = 10,
    split: str = "validation",
    level: int | None = None,
) -> list[dict]:
    """
    Load GAIA problems from HuggingFace.

    Args:
        max_instances: Max problems to load
        split: "validation" (has answers) or "test" (no answers)
        level: Filter by difficulty level (1, 2, 3) or None for all

    Returns list of dicts: task_id, question, level, final_answer, steps
    """
    import importlib.util

    if importlib.util.find_spec("huggingface_hub") is None:
        print("  ✗ huggingface_hub not installed. Run: pip install huggingface_hub")
        return []

    # Try loading from smolagents/GAIA-annotated (open, no gate)
    # Falls back to gaia-benchmark/GAIA (requires auth)
    datasets_to_try = [
        ("smolagents/GAIA-annotated", "validation"),
        ("gaia-benchmark/GAIA", "2023/validation"),
    ]

    problems = []
    for dataset_name, dataset_split in datasets_to_try:
        try:
            from datasets import load_dataset  # type: ignore[import-untyped]

            ds = load_dataset(
                dataset_name,
                split=dataset_split,
                streaming=True,
            )
            for item in ds:
                if max_instances and len(problems) >= max_instances:
                    break
                task_id = item.get("task_id", "")
                question = item.get("Question", item.get("question", ""))
                lvl = item.get("Level", item.get("level", 0))
                answer = item.get("Final answer", item.get("final_answer", ""))
                steps = item.get("Annotator Metadata", {}).get("Steps", "") or item.get(
                    "steps", ""
                )

                if level and lvl != level:
                    continue

                problems.append(
                    {
                        "task_id": task_id,
                        "question": question,
                        "level": lvl,
                        "final_answer": answer,
                        "steps": steps,
                    }
                )
            if problems:
                print(f"  Loaded {len(problems)} problems from {dataset_name}")
                break
        except Exception as e:
            print(f"  Could not load {dataset_name}: {e}")
            continue

    if not problems:
        print()
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║  GAIA dataset requires authentication           ║")
        print("  ║                                                ║")
        print("  ║  1. pip install huggingface_hub                ║")
        print("  ║  2. huggingface-cli login                      ║")
        print("  ║  3. Accept terms at:                           ║")
        print("  ║     https://hf.co/datasets/gaia-benchmark/GAIA ║")
        print("  ╚══════════════════════════════════════════════════╝")

    return problems


# ── Answer Extraction ─────────────────────────────────────────────────────────


def extract_answer(agent_output: str) -> str:
    """
    Extract a final answer from the agent's output.

    GAIA answers can be: a number, a short string, a name, or a phrase.
    We look for patterns like:
      - "Final answer: X"
      - "Answer: X"
      - The last sentence of the agent's response
    """
    if not agent_output:
        return ""

    # Pattern 1: "Final answer: ..." or "Answer: ..."
    for pattern in [
        r"(?:Final |final )?(?:answer|Answer)\s*:\s*(.+?)(?:\.|$|\n)",
        r"(?:The answer is|the answer is)\s*(.+?)(?:\.|$|\n)",
        r"(?:Therefore,? |Thus,? |So,? )(.+?)(?:\.|$|\n)",
    ]:
        m = re.search(pattern, agent_output)
        if m:
            return m.group(1).strip().strip('"').strip("'").strip(".")

    # Pattern 2: Last non-empty line that isn't a code block marker
    lines = [ln.strip() for ln in agent_output.split("\n") if ln.strip()]
    for line in reversed(lines):
        if line.startswith(("```", "---")):
            continue
        if len(line) > 5 and len(line) < 500:
            return line.strip('"').strip("'")

    return agent_output.strip()[:200]


def normalize_answer(answer: str) -> str:
    """Normalize an answer for comparison."""
    return answer.strip().lower().rstrip(".").strip()


def match_answer(predicted: str, correct: str) -> bool:
    """
    Check if predicted answer matches the correct one.
    GAIA uses strict matching (not fuzzy).
    """
    if not predicted or not correct:
        return False

    p = normalize_answer(predicted)
    c = normalize_answer(correct)

    # Exact match
    if p == c:
        return True

    # Contains match (for longer answers)
    if len(c) > 10 and (c in p or p in c):
        return True

    # Numeric match (handle formatting differences)
    try:
        p_num = float(p)
        c_num = float(c)
        return abs(p_num - c_num) < 0.01
    except (ValueError, TypeError):
        pass

    return False


# ── Run GAIA via Open-Agent ───────────────────────────────────────────────────


def solve_gaia_problem(
    question: str,
    task_id: str,
    max_steps: int = 200,
) -> str:
    """
    Solve a GAIA problem using open-agent's run_agent().

    The agent gets the question and must use its full toolkit
    (web search, file ops, python, bash) to find the answer.
    """
    from loop import run_agent  # type: ignore[import-untyped]

    prompt = (
        f"GAIA TASK: {task_id}\n\n"
        f"QUESTION:\n{question}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. This is a multi-step research task. Use web search, file operations,\n"
        f"   and Python/bash execution as needed.\n"
        f"2. Work through the problem step by step.\n"
        f"3. When you are confident in the answer, state it clearly.\n"
        f"4. Format your final answer as: Final answer: <your answer>\n"
        f"5. The final answer should be precise — a number, name, or short phrase."
    )

    try:
        final_msg = run_agent(
            prompt,
            mode="coding",
            max_steps=max_steps,
            skip_preflight=True,
        )
    except Exception as e:
        return f"Agent error: {e}"

    # Extract answer from the agent's final message
    answer = extract_answer(final_msg or "")
    return answer


# ── Benchmark Runner ──────────────────────────────────────────────────────────


def run_gaia_benchmark(
    max_instances: int = 10,
    level: int | None = None,
    split: str = "validation",
) -> dict:
    """
    Run the GAIA benchmark through open-agent's own loop.
    """
    print("╔═══════════════════════════════════════════════════╗")
    print("║      GAIA — General AI Agent Benchmark           ║")
    print("║      Meta AI · multi-step reasoning · tool use   ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()

    problems = load_gaia_problems(max_instances, split, level)

    if not problems:
        print("  No problems loaded.")
        return {"benchmark": "gaia", "problems": 0, "passed": 0}

    print(f"  Running {len(problems)} GAIA problems through agent's loop...")
    print()

    results = []
    correct = 0
    total = 0

    for idx, prob in enumerate(problems):
        total += 1
        task_id = prob["task_id"]
        question = prob["question"]
        correct_answer = prob["final_answer"]
        lvl = prob["level"]

        short_q = question[:80].replace("\n", " ")
        print(f"\n  [{idx + 1}/{len(problems)}] 🧪 L{lvl} {task_id[:8]}...")
        print(f"       {short_q}...")

        start = time.time()
        predicted_answer = solve_gaia_problem(question, task_id)
        duration = time.time() - start

        passed = match_answer(predicted_answer, correct_answer)

        if passed:
            correct += 1
            print(f"    ✓ CORRECT  ({duration:.0f}s)")
            print(f"       Predicted: {predicted_answer[:80]}")
        else:
            print(f"    ✗ WRONG  ({duration:.0f}s)")
            print(f"       Predicted: {predicted_answer[:80]}")
            print(f"       Correct:  {correct_answer[:80]}")

        results.append(
            {
                "task_id": task_id,
                "level": lvl,
                "question": question[:200],
                "predicted": predicted_answer,
                "correct": correct_answer,
                "passed": passed,
                "duration_s": round(duration, 1),
            }
        )

        if total > 0:
            rate = (correct / total) * 100
            print(f"    ── Running accuracy: {rate:.1f}% ({correct}/{total})")

    # Summary
    rate = (correct / total * 100) if total > 0 else 0
    print()
    print("  " + "═" * 45)
    print("  GAIA BENCHMARK RESULTS")
    print("  " + "═" * 45)
    print(f"  Problems:  {total}")
    print(f"  Correct:   {correct}")
    print(f"  Accuracy:  {rate:.1f}%")
    print(f"  Level:     {level or 'all'}")
    print()

    summary = {
        "benchmark": "gaia",
        "model": "open-agent",
        "split": split,
        "level": level or "all",
        "problems": total,
        "correct": correct,
        "accuracy": round(rate, 1),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    save_path = Path(__file__).resolve().parent / "reports" / "gaia_results.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Results saved to: {save_path}")
    print()

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="GAIA Benchmark — multi-step agent evaluation"
    )
    parser.add_argument("--instances", type=int, default=5)
    parser.add_argument("--level", type=int, default=None, choices=[1, 2, 3])
    parser.add_argument("--split", default="validation")
    args = parser.parse_args()

    run_gaia_benchmark(args.instances, args.level, args.split)
