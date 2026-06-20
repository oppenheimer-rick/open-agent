"""
SWE-bench Lite — Software Engineering Benchmark (open-agent native)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Industry standard for autonomous code repair (used by OpenAI, Anthropic,
DeepSeek, Qwen). The agent clones a repo, explores the codebase, applies
a fix, and produces a git patch — evaluated via official Docker harness.

Two-phase pipeline:
  1. Generate patches via open-agent's ReAct loop inside each cloned repo
  2. Evaluate patches using swebench's official Docker-based evaluator

Usage:
  python -m benchmark.swebench --instances 5              # Phase 1
  python -m benchmark.swebench --evaluate                  # Phase 2 only
  python -m benchmark.swebench --instances 5 --evaluate    # Both phases

From loop.py REPL:
  /benchmark swebench --instances 5
  /benchmark swebench --instances 5 --evaluate

Dependencies: pip install datasets GitPython swebench docker
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path


# ── Imports (lazy) ────────────────────────────────────────────────────────────

try:
    import git  # type: ignore[import-untyped]
except ImportError:
    git = None  # type: ignore[assignment]

try:
    from datasets import load_dataset  # type: ignore[import-untyped]
except ImportError:
    load_dataset = None  # type: ignore[assignment]


# ── Config ─────────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_INSTANCES: int | None = 5
OUTPUT_DIR = HERE / "reports"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(HERE / "swebench_run.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── Phase 1: Generate Patches ────────────────────────────────────────────────


def run_agent_in_repo(
    issue_text: str, instance_id: str, repo_path: Path
) -> tuple[str, float]:
    """Call run_agent() directly, changing cwd to the cloned repo."""
    from loop import run_agent  # type: ignore[import-untyped]

    prompt = (
        f"MISSION ID: {instance_id}\n\n"
        f"GOAL: Resolve the following software bug.\n\n"
        f"ISSUE DESCRIPTION:\n{issue_text}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Explore the codebase using file operations and grep.\n"
        f"2. Understand the bug from the issue description.\n"
        f"3. Apply a permanent fix using patch_file.\n"
        f"4. Verify the fix if possible.\n"
        f"5. Once finished, provide a summary of your changes."
    )

    saved_cwd = Path.cwd()
    try:
        os.chdir(str(repo_path))
        start = time.time()
        run_agent(
            prompt,
            mode="coding",
            max_steps=500,
            skip_preflight=True,
        )
        duration = time.time() - start
    except Exception as e:
        duration = 0.0
        log.error("Error in run_agent for %s: %s", instance_id, e)
        return "ERROR", duration
    finally:
        os.chdir(str(saved_cwd))

    return "OK", duration


def generate_patches(max_instances: int | None = None):
    """Phase 1: Clone repos and generate patches via open-agent."""
    if load_dataset is None:
        log.error("datasets not installed. Run: pip install datasets")
        return
    if git is None:
        log.error("GitPython not installed. Run: pip install GitPython")
        return

    if max_instances is None:
        max_instances = DEFAULT_INSTANCES

    save_path = OUTPUT_DIR / f"swe_predictions_{int(time.time())}.jsonl"
    log.info("=== SWE-bench Phase 1: Agent Patch Generation ===")
    log.info("Max instances: %s", max_instances)

    dataset = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    if max_instances is not None:
        dataset = dataset.select(range(min(max_instances, len(dataset))))

    log.info("Loaded %d instances", len(dataset))

    # Resume support
    completed_ids: set[str] = set()
    if save_path.exists():
        with open(save_path) as f:
            for line in f:
                if line.strip():
                    completed_ids.add(json.loads(line)["instance_id"])
        log.info("Already completed: %d", len(completed_ids))

    for instance in dataset:  # type: ignore[union-attr]
        instance_id = instance["instance_id"]  # type: ignore[index]
        if instance_id in completed_ids:
            continue

        log.info(
            "🚀 %s  %s  (%s)",
            instance_id,
            instance["repo"],  # type: ignore[index]
            instance.get("base_commit", "?")[:7],  # type: ignore[union-attr]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "repo"
            try:
                repo = git.Repo.clone_from(
                    f"https://github.com/{instance['repo']}.git",  # type: ignore[index]
                    repo_path,
                )
                repo.git.checkout(instance["base_commit"])  # type: ignore[index]

                status, duration = run_agent_in_repo(
                    instance["problem_statement"],  # type: ignore[index]
                    instance_id,
                    repo_path,
                )

                diff = repo.git.diff()

                prediction = {
                    "instance_id": instance_id,
                    "model_patch": diff,
                    "model_name_or_path": "open-agent",
                    "version": "1.0",
                    "repo": instance["repo"],  # type: ignore[index]
                    "base_commit": instance["base_commit"],  # type: ignore[index]
                    "stats": {
                        "duration_sec": round(duration, 1),
                        "patch_size": len(diff.splitlines()),
                        "agent_status": status,
                    },
                }

                with open(save_path, "a") as f:
                    f.write(json.dumps(prediction, indent=None) + "\n")

                log.info(
                    "✅ %s done in %.1fs  (patch: %d lines)",
                    instance_id,
                    duration,
                    len(diff.splitlines()),
                )

            except Exception as e:
                log.error("❌ Error on %s: %s", instance_id, e)

    log.info("=== Phase 1 Complete ===")
    log.info("Predictions saved to: %s", save_path)
    return save_path


# ── Phase 2: Official Docker Evaluation ───────────────────────────────────────


def run_evaluation(
    predictions_path: str | None = None,
    max_workers: int = 4,
    force_rebuild: bool = False,
):
    """Phase 2: Run the official SWE-bench Docker evaluation."""
    if predictions_path is None:
        # Find latest predictions file
        pred_files = sorted(OUTPUT_DIR.glob("swe_predictions_*.jsonl"))
        if not pred_files:
            log.error("No predictions file found. Run Phase 1 first.")
            return
        predictions_path = str(pred_files[-1])

    log.info("=== SWE-bench Phase 2: Official Docker Evaluation ===")
    log.info("Predictions: %s", predictions_path)
    log.info("Workers:     %d", max_workers)

    # Check Docker
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.error("Docker is not available. Install Docker and start the daemon.")
        return

    try:
        from swebench.harness.run_evaluation import main as run_eval  # type: ignore[import-untyped]  # noqa: I001
    except ImportError:
        log.error("swebench not installed. Run: pip install swebench")
        return

    # Build instance_ids list
    instance_ids = []
    if Path(predictions_path).exists():
        with open(predictions_path) as f:
            for line in f:
                if line.strip():
                    instance_ids.append(json.loads(line)["instance_id"])

    if not instance_ids:
        log.warning("No predictions found in %s", predictions_path)
        return

    log.info("Evaluating %d instances...", len(instance_ids))

    run_id = f"open-agent-{time.strftime('%Y%m%d-%H%M%S')}"

    run_eval(
        dataset_name="princeton-nlp/SWE-bench_Lite",
        split="test",
        instance_ids=instance_ids,
        predictions_path=predictions_path,
        max_workers=max_workers,
        force_rebuild=force_rebuild,
        cache_level="env",
        clean=False,
        open_file_limit=4096,
        run_id=run_id,
        timeout=3600,
        namespace=None,
        rewrite_reports=False,
        modal=False,
    )

    log.info("=== Phase 2 Complete ===")
    log.info("Run ID: %s", run_id)


# ── CLI ───────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="SWE-bench Lite — open-agent patch generation + Docker evaluation",
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=DEFAULT_INSTANCES,
        help="Number of SWE-bench instances to process",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Run official Docker evaluation after generation",
    )
    parser.add_argument(
        "--predictions",
        type=str,
        default=None,
        help="Path to existing predictions file (evaluate-only mode)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Max parallel Docker workers",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Force rebuild Docker images",
    )
    args = parser.parse_args()

    if args.predictions:
        run_evaluation(
            predictions_path=args.predictions,
            max_workers=args.workers,
            force_rebuild=args.force_rebuild,
        )
    else:
        save_path = generate_patches(max_instances=args.instances)

        if args.evaluate and save_path:
            run_evaluation(
                predictions_path=str(save_path),
                max_workers=args.workers,
                force_rebuild=args.force_rebuild,
            )
