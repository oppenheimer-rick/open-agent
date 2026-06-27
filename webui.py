#!/usr/bin/env python3
"""
Web UI backend for open-agent.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FastAPI server that serves the SPA (index.html) and provides WebSocket +
REST endpoints for streaming agent interactions in the browser.

Usage:
  python -m uvicorn webui:app --host 0.0.0.0 --port 8000 --reload
  # or via the slash command /webui inside the REPL
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

try:
    import uvicorn
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
except ImportError:
    raise ImportError(
        "Missing web dependencies. Install with: pip install fastapi uvicorn websockets"
    )

# ── Import agent core components ───────────────────────────────────────────────
# These get re-used from loop.py without the REPL/CLI layer
import providers

# We'll leverage the existing system prompt, TOOLS and TOOL_MAP from loop
import importlib.util
import sys

LOOP_PATH = Path(__file__).parent / "loop.py"

def _load_loop_module():
    """Load loop.py as a module (not __main__), grabbing its system components."""
    spec = importlib.util.spec_from_file_location("_loop_core", LOOP_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load loop.py from {LOOP_PATH}")
    mod = importlib.util.module_from_spec(spec)
    # Patch sys.argv so loop.py doesn't parse args on import
    old_argv = sys.argv
    sys.argv = ["loop.py", "--mode", "browser"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
    return mod

loop_mod = _load_loop_module()

# Extract what we need
TOOL_MAP = loop_mod.TOOL_MAP
TOOLS = loop_mod.TOOLS
PHILOSOPHY = loop_mod.PHILOSOPHY
SYSTEM_GENERAL = loop_mod.SYSTEM_GENERAL
ootb = loop_mod.ootb
ITERATION_COUNT = getattr(loop_mod, "ITERATION_COUNT", 0)
compress_past_tools = getattr(loop_mod, "compress_past_tools", lambda x: x)
repair_json_args = getattr(loop_mod, "repair_json_args", lambda x, fn: x)

# ── State ──────────────────────────────────────────────────────────────────────

CONVERSATIONS_FILE = Path(".web_conversations.json")
MAX_STEPS = int(os.environ.get("WEB_MAX_STEPS", "50"))


def _load_conversations() -> dict:
    if CONVERSATIONS_FILE.exists():
        try:
            return json.loads(CONVERSATIONS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_conversations(data: dict):
    CONVERSATIONS_FILE.write_text(json.dumps(data, indent=2))


# ── FastAPI App ────────────────────────────────────────────────────────────────

app = FastAPI(title="open-agent", version="2.2.0")


# ── Static Routes ──────────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent


@app.get("/")
async def serve_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ── REST API ───────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "provider": providers.PROVIDER_ACTUAL,
        "model": providers.get_model(),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/conversations")
async def list_conversations():
    convs = _load_conversations()
    result = []
    for cid, c in sorted(
        convs.items(),
        key=lambda x: x[1].get("timestamp", ""),
        reverse=True,
    ):
        messages = c.get("messages", [])
        preview = ""
        if messages:
            first_user = next(
                (m["content"] for m in messages if m.get("role") == "user"), None
            )
            if first_user:
                preview = first_user[:80] + ("..." if len(first_user) > 80 else "")
        result.append(
            {
                "id": cid,
                "title": c.get("title") or preview or "New Chat",
                "preview": preview,
                "timestamp": c.get("timestamp", ""),
                "message_count": len(messages),
            }
        )
    return result


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str):
    convs = _load_conversations()
    c = convs.get(cid)
    if c is None:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)
    return {
        "id": cid,
        "title": c.get("title", "New Chat"),
        "messages": c.get("messages", []),
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────


@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
    active_conversation_id = None

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type", "")

            if msg_type == "abort":
                # Signal the agent loop to stop
                # Currently we handle this by short-circuiting
                await ws.send_json({"type": "done"})
                continue

            if msg_type == "message":
                user_text = data.get("content", "").strip()
                active_conversation_id = data.get(
                    "conversation_id"
                ) or f"conv_{uuid.uuid4().hex[:12]}"

                # Load or create conversation
                convs = _load_conversations()
                conv = convs.get(active_conversation_id, {
                    "id": active_conversation_id,
                    "title": user_text[:60],
                    "timestamp": datetime.now().isoformat(),
                    "messages": [],
                })

                # Add user message
                conv["messages"].append({
                    "role": "user",
                    "content": user_text,
                    "id": f"msg_{uuid.uuid4().hex[:8]}",
                    "timestamp": datetime.now().isoformat(),
                })

                # Extract message history
                messages = await _build_messages(conv)

                # Run agent loop
                await _run_agent_loop(ws, messages, conv, active_conversation_id, convs)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ── Agent Loop (WebSocket version) ─────────────────────────────────────────────


async def _build_messages(conv: dict) -> list:
    """Build the OpenAI-style message list from conversation history, injecting system prompt."""
    system_msg = {"role": "system", "content": SYSTEM_GENERAL}

    # Inject OOTB context
    try:
        shadow = ootb.get_shadow_context()
        if shadow:
            system_msg["content"] += "\n\n--- CONTEXT ---\n" + shadow
    except Exception:
        pass

    messages = [system_msg]

    for m in conv.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content", "")
        if role in ("user", "assistant"):
            msg = {"role": role, "content": content}
            if role == "assistant" and m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
            messages.append(msg)
        elif role == "tool":
            messages.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id", ""),
                "content": m.get("content", ""),
            })

    return messages


async def _run_agent_loop(
    ws: WebSocket,
    messages: list,
    conv: dict,
    conv_id: str,
    convs: dict,
):
    """Run the agent loop, streaming events over WebSocket."""
    current_assistant_id = f"msg_{uuid.uuid4().hex[:8]}"
    full_assistant_content = ""
    step = 0

    await ws.send_json({"type": "status", "phase": "thinking"})

    while step < MAX_STEPS:
        step += 1

        # ── Context management ──
        new_messages = compress_past_tools(messages)
        if new_messages is not messages:
            messages.clear()
            messages.extend(new_messages)

        # ── LLM call (streaming) ──
        try:
            # Sanitize history
            for m in messages:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        raw = tc["function"].get("arguments", "{}")
                        tc["function"]["arguments"] = repair_json_args(
                            raw, tc["function"]["name"]
                        )

            response_message, _, _ = None, "stop", {}
            async for event in _async_iter_chat_events(messages):
                if event["type"] == "error":
                    await ws.send_json({
                        "type": "error",
                        "message": event["message"],
                    })
                    return
                await ws.send_json(event)
                if event["type"] == "assistant_done":
                    response_message = event["message"]
                    _ = event.get("finish_reason", "stop")
                    _ = event.get("usage", {})

            if response_message is None:
                await ws.send_json({
                    "type": "error",
                    "message": "No response from LLM",
                })
                return

        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return

        # Track content
        content = response_message.get("content", "") or ""
        if content:
            full_assistant_content += content

        tool_calls = response_message.get("tool_calls") or []

        # If no tool calls, we're done
        if not tool_calls:
            break

        # ── Execute tool calls ──
        await ws.send_json({"type": "status", "phase": "executing_tools"})

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", "{}")

            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = repair_json_args(raw_args, fn_name)
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}

            # Notify frontend
            tool_event = {
                "type": "tool_call",
                "id": tc.get("id", fn_name),
                "tool": fn_name,
                "args": args,
            }
            await ws.send_json(tool_event)

            # Execute
            handler = TOOL_MAP.get(fn_name)
            if handler:
                try:
                    result = handler(args)
                    error = str(result).startswith("ERROR")
                except Exception as e:
                    result = f"TOOL EXCEPTION: {e}"
                    error = True
            else:
                result = f"UNKNOWN TOOL: {fn_name}"
                error = True

            await ws.send_json({
                "type": "tool_result",
                "id": tc.get("id", fn_name),
                "status": "error" if error else "done",
            })

            # Add tool call to assistant message
            tc_response = {
                "role": "assistant",
                "content": None,
                "tool_calls": [tc],
            }
            messages.append(tc_response)

            # Add tool result
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", fn_name),
                "content": result[:5000] if len(result) > 5000 else result,
            })

    # ── Done ──
    # Save assistant message to conversation
    conv["messages"].append({
        "role": "assistant",
        "content": full_assistant_content,
        "id": current_assistant_id,
        "timestamp": datetime.now().isoformat(),
    })

    # Update conversation
    conv["timestamp"] = datetime.now().isoformat()
    convs[conv_id] = conv
    _save_conversations(convs)

    await ws.send_json({"type": "done"})
    await ws.send_json({"type": "status", "phase": "idle"})


async def _async_iter_chat_events(messages: list):
    """
    Async wrapper around the synchronous iter_chat_events.
    Runs the sync generator in a thread and yields events.
    """
    import asyncio
    import functools

    gen = functools.partial(loop_mod.iter_chat_events, messages, TOOLS)
    loop = asyncio.get_event_loop()
    it = await loop.run_in_executor(None, gen)

    while True:
        try:
            event = await loop.run_in_executor(None, next, it)
            yield event
            if event["type"] == "assistant_done":
                break
        except StopIteration:
            break
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            break


# ── CLI entry ──────────────────────────────────────────────────────────────────


def run_webui(host: str = "0.0.0.0", port: int = 8000):
    """Start the web server. Called from the REPL via /webui."""
    print(f"  🌐 open-agent Web UI: http://{host}:{port}")
    print("  💡 Open in browser. Press Ctrl+C to stop.")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_webui()
