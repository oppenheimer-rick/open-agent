#!/usr/bin/env python3
import sys
import argparse
from ui.repl import repl_loop
from tools.registry import synthesize_tool, TOOL_MAP, TOOLS
from core.agent import run_agent

def _parse_args():
    p = argparse.ArgumentParser(
        description="openagent — local-first terminal IDE agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  openagent                             # interactive REPL
  op "what is 2+2"                      # quick one-shot using short shortcut
  openagent "refactor utils.py" --coding # structured coding agent
  op "explain this codebase" -s 20
        """,
    )
    p.add_argument(
        "task", nargs="?", default=None, help="Task to run (omit for interactive)"
    )
    p.add_argument("--coding", "-c", action="store_true", help="Use coding agent mode")
    p.add_argument(
        "--steps", "-s", type=int, default=200, help="Max steps (default 200)"
    )
    p.add_argument(
        "--debug-stream",
        action="store_true",
        help="Show raw SSE chunk timing and fields",
    )
    p.add_argument(
        "--search", action="store_true", help="Run search_web directly for diagnostics"
    )
    p.add_argument(
        "--update", action="store_true", help="Update open-agent to the latest version"
    )
    p.add_argument(
        "--offline", action="store_true", help="Run in offline mode, bypassing all remote checks and network MCP servers"
    )
    return p.parse_args()

def main():
    args = _parse_args()
    
    import os
    import mcp_client
    if getattr(args, "offline", False) or not mcp_client.is_online():
        os.environ["OPENAGENT_OFFLINE"] = "1"
    
    if getattr(args, "update", False):
        from ui.repl import update_openagent
        update_openagent()
        return

    mode = "coding" if args.coding else "general"
    
    repl_loop(
        mode_default=mode,
        max_steps_default=args.steps,
        debug_stream_flag=args.debug_stream,
        run_search_flag=args.search,
        run_task_flag=args.task
    )

if __name__ == "__main__":
    main()
