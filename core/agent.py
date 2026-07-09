import os
import re
import time
import json
import httpx
from datetime import datetime
from pathlib import Path
from collections import Counter

from ui.console import C, co, dim, ui_header, ui_info, ui_error, ui_step_banner, ConsoleRenderer
from core.prompts import SYSTEM_CODING, SYSTEM_GENERAL
from core.config import SESSIONS_DIR
from core.session import session_save
from tools.registry import TOOLS, TOOL_MAP, tool_result_append
from tools.sentinel import sentinel_map_codebase
from core.state import ACTIVE_FILES, AGENT_MODIFIED_FILES, ACTIVE_MESSAGES, FILE_CHANGES_QUEUE
import providers
import mission
from memory import auto_context as auto_memory_context, save as memory_save

# Global constants for loop limits
TODO_FILE = ".agent_todo.json"
MAX_STEPS = 500  # hard cap on agentic loop iterations
PYTHON_TIMEOUT = 30  # seconds for sandboxed python

class AgentInterrupted(Exception):
    """Raised when the user interrupts an active agent run."""

def repair_json_args(raw_args: str, fn_name: str = "") -> str:
    """Attempt to repair truncated or malformed JSON arguments from local LLMs."""
    fixed = raw_args.strip()

    # Strip markdown code block formatting if present
    if fixed.startswith("```"):
        lines = fixed.splitlines()
        if len(lines) > 1 and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        fixed = "\n".join(lines).strip()

    # 1. Fix unescaped newlines/tabs which local LLMs often output raw
    fixed = fixed.replace("\n", "\\n").replace("\t", "\\t")

    # 2. Balance quotes
    if fixed.count('"') % 2 != 0:
        fixed += '"'

    # 3. Ensure it ends with a brace
    if not fixed.endswith("}"):
        if not fixed.endswith('"'):
            fixed += '"'
        fixed += "}"

    # 4. Final attempt to parse. If it fails, try a more aggressive approach
    try:
        json.loads(fixed)
        return fixed
    except json.JSONDecodeError:
        import ast
        try:
            parsed = ast.literal_eval(fixed)
            if isinstance(parsed, dict):
                return json.dumps(parsed)
        except Exception:
            pass
        return raw_args

def llm_generate(system_prompt: str, user_prompt: str, max_tokens: int = 512) -> str:
    """Direct, synchronous call to the LLM backend via provider abstraction."""
    return providers.generate(system_prompt, user_prompt, max_tokens)

def iter_chat_events(messages: list, tools: list = None):
    """Stream chat completions from the configured provider."""
    try:
        yield from providers.iter_chat_events(messages, tools)
    except KeyboardInterrupt:
        yield {"type": "error", "message": "Interrupted by user."}
        raise AgentInterrupted()

def stream_chat(
    messages: list, tools: list = None, renderer: ConsoleRenderer = None
) -> tuple:
    """Stream from the configured provider."""
    renderer = renderer or ConsoleRenderer()
    final = None
    try:
        for event in iter_chat_events(messages, tools):
            renderer.handle(event)
            if event.get("type") == "assistant_done":
                final = event
    except KeyboardInterrupt:
        raise AgentInterrupted()
    finally:
        renderer.stop_spinner()

    if not final:
        raise RuntimeError("LLM stream ended without a final assistant message")
    return final["message"], final["finish_reason"], final["usage"]

def debug_stream(user_message: str):
    """Print raw stream timing so UI delays can be separated from server delays."""
    url = providers.get_chat_url()
    llm_base = providers.BASE_URL
    temperature = providers.get_temperature()
    print(dim(f"POST {url}  [{providers.status_line()}]"))
    started = last = time.monotonic()
    payload = {
        "model": providers.get_model(),
        "messages": [
            {"role": "system", "content": "Reply directly and briefly."},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": 512,
        "stream": True,
    }
    with httpx.Client(timeout=180) as client:
        with client.stream(
            "POST", f"{llm_base}/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                now = time.monotonic()
                if not raw_line.startswith("data: "):
                    continue
                data = raw_line[6:].strip()
                if data == "[DONE]":
                    print(dim(f"[{now - started:6.2f}s +{now - last:5.2f}s] DONE"))
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    print(co(C.RED, f"[{now - started:6.2f}s] bad json: {data[:100]}"))
                    last = now
                    continue
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                keys = ",".join(delta.keys()) or "-"
                content = (
                    delta.get("content")
                    or delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("thinking")
                    or ""
                )
                print(
                    dim(
                        f"[{now - started:6.2f}s +{now - last:5.2f}s] keys={keys} text={repr(content[:70])}"
                    )
                )
                last = now

def compress_past_tools(history: list) -> list:
    """Compress older tool calls/responses from past turns to save context space."""
    if len(history) <= 6:
        return history
        
    compressed = []
    keep_intact_start_idx = len(history) - 6
    
    for i, m in enumerate(history):
        if i < 2 or i >= keep_intact_start_idx:
            compressed.append(m)
        else:
            m_copy = m.copy()
            role = m_copy.get("role")
            if role == "tool":
                content = m_copy.get("content", "")
                if len(content) > 600:
                    m_copy["content"] = content[:500] + f"\n... [TRUNCATED {len(content) - 500} CHARS OF OLD TOOL OUTPUT TO CONSERVE CONTEXT] ..."
                compressed.append(m_copy)
            elif role == "assistant":
                if "tool_calls" in m_copy and m_copy["tool_calls"]:
                    if m_copy.get("content"):
                        m_copy.pop("tool_calls", None)
                compressed.append(m_copy)
            else:
                compressed.append(m_copy)
    return compressed

def manage_context(history: list, limit: int = 80):
    """Keep system prompt, grounding, and original user mission, but roll history."""
    history = compress_past_tools(history)

    if len(history) <= limit:
        return history

    header = history[:5]
    footer = history[-(limit - 5) :]

    mid_drop = len(history) - len(header) - len(footer)
    print(co(C.YELLOW, f"\n  ⚠️ WARNING: Context limit reached ({len(history)} messages). Archiving {mid_drop} past turns to keep history lean..."))

    return (
        header
        + [
            {
                "role": "system",
                "content": (
                    f"... [{mid_drop} messages archived for context efficiency] ...\n"
                    "SYSTEM NOTICE: Earlier conversation turns have been archived to prevent context window overflow. "
                    "If you need to recall specific code, files, or decisions from earlier turns, please use 'search_second_brain' or ask the user."
                ),
            }
        ]
        + footer
    )

def smart_search(topic: str, count: int = 1) -> str:
    """Dynamically brainstorm queries and perform multi-angle web search."""
    from web_search import search_web
    print(co(C.CYAN, f"  🧠 SmartSearch brainstorming {count} angle(s) for: '{topic}'..."))
    prompt = (
        "You are an expert search query architect. Given a search request, brainstorm "
        "highly specific keyword query variants to extract high-density facts. Keep queries under 6 words.\n\n"
        "Return a JSON list of strings, for example: [\"query variant 1\", \"query variant 2\", ...]"
    )
    res = llm_generate(prompt, f"Topic: {topic}", max_tokens=128)
    try:
        queries = json.loads(res.strip())
        if not isinstance(queries, list):
            queries = [topic]
    except Exception:
        # basic regex extractor if JSON parse fails
        queries = re.findall(r'"([^"]+)"', res)
        if not queries:
            queries = [topic]

    queries = queries[:count]
    combined_results = []
    for q in queries:
        print(dim(f"    🔍 Searching angle: {q}"))
        search_res = search_web(q, max_results=2)
        # Propagate STOP signal immediately — no point searching more queries
        if search_res.startswith("STOP:"):
            return search_res
        combined_results.append(f"=== {q} ===\n{search_res}")
        time.sleep(0.1)

    return "\n\n".join(combined_results)

def run_agent(
    user_message: str,
    mode: str = "general",
    max_steps: int = 50,
    skip_preflight: bool = False,
    chat_history: list = None,
):
    """Main agentic ReAct runner loop."""
    cwd = Path.cwd()
    projects_dir = cwd / "Projects"
    projects_dir.mkdir(exist_ok=True)

    # Auto-inject MCP tools if available
    try:
        import mcp_client
        mcp_tools, mcp_handlers = mcp_client.connect_and_register()
        if mcp_tools:
            TOOLS.extend(mcp_tools)
            TOOL_MAP.update(mcp_handlers)
    except Exception:
        pass

    now = datetime.now()
    grounding = (
        f"CONTEXT AWARENESS ({now.strftime('%Y-%m-%d')}):\n"
        f"- CURRENT_TIME: {now.strftime('%H:%M:%S')}\n"
        f"- CURRENT_DATE: {now.strftime('%A, %B %d, %Y')}\n"
        f"- WORKING_DIR: {cwd}\n"
        f"- PROJECTS_DIR: {projects_dir}\n"
        f"- SYSTEM_ADVISORY: Use the PROJECTS_DIR for all new code files unless explicitly directed otherwise.\n"
    )

    system = SYSTEM_CODING if mode == "coding" else SYSTEM_GENERAL
    memory_context = auto_memory_context(user_message)

    if chat_history is None:
        chat_history = []

    is_fresh_session = not chat_history

    global ACTIVE_MESSAGES
    if is_fresh_session:
        messages = chat_history
        messages.extend([
            {"role": "system", "content": system},
            {"role": "system", "content": grounding},
        ])
        if memory_context:
            messages.append({"role": "system", "content": memory_context})

        from ui.diagnostics import check_and_summarize_obsidian_vault
        obsidian_insights = check_and_summarize_obsidian_vault(silent=True)
        if obsidian_insights:
            messages.append({
                "role": "system",
                "content": f"OBSIDIAN VAULT RECENT CHANGES & INSIGHTS:\n{obsidian_insights}"
            })

        if mode == "coding":
            ui_info("Engaging Architectural Sentinel...")
            blueprint = sentinel_map_codebase()
            messages.append(
                {
                    "role": "system",
                    "content": f"MISSION START GROUNDING (Global Blueprint):\n{blueprint}",
                }
            )

        # Warm up the LLM KV cache with the system prompts / codebase grounding
        from ui.console import Spinner
        spinner = Spinner("Booting LLM...")
        spinner.start()
        try:
            prewarm_msgs = messages + [{"role": "user", "content": 'Reply with "yes" if you are here for me'}]
            providers.generate(
                system_prompt=None,
                user_prompt=None,
                max_tokens=10,
                messages=prewarm_msgs
            )
        except Exception:
            pass
        finally:
            spinner.stop()

        title = "CODING MISSION" if mode == "coding" else "AGENT SESSION"
        ui_header(title, user_message[:70])
        ACTIVE_MESSAGES = messages
    else:
        messages = chat_history
        ui_header("CONTINUING MISSION", user_message[:70])
        ACTIVE_MESSAGES = messages

    messages.append({"role": "user", "content": user_message})

    total_in = total_out = 0
    step = 0
    renderer = ConsoleRenderer()
    final_message = ""

    _tool_call_history = []
    _completed_writes = set()
    _write_completion_signaled = set()
    stop_search = False
    consecutive_nudges = 0

    if is_fresh_session:
        mission.migrate_from_todo_file()
        mission_state = mission.render()
        if mission_state:
            messages.append(
                {
                    "role": "system",
                    "content": f"ACTIVE MISSION:\n{mission_state}",
                }
            )

        if Path(TODO_FILE).exists():
            try:
                os.replace(TODO_FILE, f"{TODO_FILE}.bak")
            except Exception:
                pass
        if Path(".mission_state.txt").exists():
            try:
                os.replace(".mission_state.txt", ".mission_state.txt.bak")
            except Exception:
                pass

    while step < max_steps:
        step += 1
        ui_step_banner(step)
        renderer.step = step

        changes_detected = []
        while not FILE_CHANGES_QUEUE.empty():
            changes_detected.append(FILE_CHANGES_QUEUE.get())

        if changes_detected:
            change_report = "SYSTEM NOTIFICATION - FILESYSTEM CHANGES DETECTED:\n"
            for c in changes_detected:
                change_report += f"- {c['path']} was {c['type']} by external editor.\n"
            ui_info(
                f"Bidirectional Sync: Detected {len(changes_detected)} file changes."
            )
            messages.append({"role": "system", "content": change_report})

        new_messages = manage_context(messages)
        if new_messages is not messages:
            messages.clear()
            messages.extend(new_messages)

        try:
            for m in messages:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        raw = tc["function"].get("arguments", "{}")
                        tc["function"]["arguments"] = repair_json_args(
                            raw, tc["function"]["name"]
                        )

            msg, finish_reason, usage = stream_chat(messages, TOOLS, renderer)
        except AgentInterrupted:
            ui_info("Interrupted. Partial output was kept above.")
            break
        except KeyboardInterrupt:
            renderer.stop_spinner()
            ui_info("Interrupted. Partial output was kept above.")
            break
        except Exception as e:
            ui_error(f"LLM call failed — aborting. Exception: {e}")
            break

        messages.append(msg)
        total_in += usage.get("prompt_tokens", 0)
        total_out += usage.get("completion_tokens", 0)

        tool_calls = msg.get("tool_calls") or []

        if not tool_calls and msg.get("content"):
            content = msg["content"]
            xml_matches = re.finditer(r"<tool_call>([\s\S]*?)</tool_call>", content)
            for match in xml_matches:
                block = match.group(1)
                fn_match = re.search(r"<function=([^>]+)>", block)
                if not fn_match:
                    continue
                fn_name = fn_match.group(1).strip()

                params = {}
                param_matches = re.finditer(
                    r"<parameter=([^>]+)>([\s\S]*?)</parameter>", block
                )
                for pm in param_matches:
                    key = pm.group(1).strip()
                    val = pm.group(2).strip()
                    params[key] = val

                tool_calls.append(
                    {
                        "id": f"xml_{int(time.time())}_{len(tool_calls)}",
                        "type": "function",
                        "function": {"name": fn_name, "arguments": json.dumps(params)},
                    }
                )

        if not tool_calls and finish_reason == "stop":
            content_str = (msg.get("content") or "").strip()
            if not content_str:
                ui_info("Agent returned empty response. Nudging to continue...")
                consecutive_nudges += 1
                if consecutive_nudges >= 3:
                    ui_error("Too many consecutive empty responses. Aborting loop.")
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": "You didn't produce any output. Please assess the situation and respond with your next action or a summary.",
                    }
                )
                continue
            elif content_str.endswith(":"):
                ui_info("Agent output ended with a trailing colon. Nudging to invoke the tool call...")
                consecutive_nudges += 1
                if consecutive_nudges >= 3:
                    ui_error("Too many consecutive nudges. Aborting loop.")
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": "You ended your response with a colon but did not invoke any tools. Please execute the tool call or action to continue the process.",
                    }
                )
                continue
            else:
                active_mission = mission.load()
                pending_objectives = [
                    o for o in active_mission.get("objectives", [])
                    if o.get("status") in ("pending", "in_progress")
                ]
                if pending_objectives:
                    ui_info("Mission has pending objectives. Presenting options to the user...")
                    final_message = (
                        "Mission Status Update: There are still pending objectives in the active mission:\n"
                        + "\n".join([f"- {o['title']}" for o in pending_objectives])
                        + "\n\nPlease let me know if you would like me to proceed with these tasks, or if we should pivot to something else."
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": final_message
                        }
                    )
                else:
                    final_message = msg.get("content") or ""
            break

        if finish_reason == "length":
            ui_info("Token limit reached. Requesting continuation...")
            truncation_count = getattr(run_agent, "_truncation_count", 0) + 1
            run_agent._truncation_count = truncation_count
            if truncation_count >= 3:
                ui_info("Too many truncations. Asking for summary.")
                messages.append(
                    {
                        "role": "user",
                        "content": "Your response was truncated again. Please provide a brief summary of what you've done so far and what remains. Do NOT make tool calls.",
                    }
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous response was truncated. Please continue exactly from where you left off (starting with the tool call if it was interrupted).",
                    }
                )
            continue
        run_agent._truncation_count = 0
        if tool_calls:
            consecutive_nudges = 0

        # Execute tool calls
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", "{}")

            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                fixed = repair_json_args(raw_args, fn_name)
                try:
                    args = json.loads(fixed)
                except json.JSONDecodeError:
                    args = {}
                    ui_error(f"Could not parse args for {fn_name}. The LLM generated invalid JSON formatting.")

            renderer.handle(
                {
                    "type": "tool_call_queued",
                    "name": fn_name,
                    "args": args,
                }
            )

            if stop_search and fn_name in ("search_web", "smart_search", "web_fetch", "scout_website"):
                result = "ERROR: Search and fetch tools are disabled for the rest of this session due to a previous stop signal."
                error = True
            else:
                handler = TOOL_MAP.get(fn_name)
                if handler:
                    try:
                        result = handler(args)
                        error = str(result).startswith("ERROR")
                    except AgentInterrupted:
                        renderer.stop_spinner()
                        ui_info("Interrupted during tool execution.")
                        return
                    except KeyboardInterrupt:
                        renderer.stop_spinner()
                        ui_info("Interrupted during tool execution.")
                        return
                    except Exception as e:
                        result = f"TOOL EXCEPTION: {e}"
                        error = True
                else:
                    result = f"ERROR: Unknown tool '{fn_name}'"
                    error = True

            renderer.handle(
                {
                    "type": "tool_call_result",
                    "name": fn_name,
                    "args": args,
                    "result": result,
                    "error": error,
                }
            )
            tool_result_append(fn_name, args, result, error)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                }
            )

            # Detect "STOP:" signal from search/fetch tools — inject hard stop
            if str(result).startswith("STOP:") and fn_name in ("search_web", "smart_search", "web_fetch", "scout_website"):
                messages.append({
                    "role": "system",
                    "content": "SYSTEM: search_web returned a STOP signal — search engines are unavailable. "
                    "Do NOT call any search or fetch tool again this session. Answer from your existing knowledge."
                })
                stop_search = True

            path_arg = args.get("path", "") if isinstance(args, dict) else ""
            _tool_call_history.append((fn_name, path_arg))
            if fn_name == "write_file" and not error and path_arg:
                _completed_writes.add(path_arg)

        new_writes = _completed_writes - _write_completion_signaled
        if new_writes:
            written = ", ".join(sorted(new_writes))
            messages.append(
                {
                    "role": "system",
                    "content": f"TASK STATUS: File(s) [{written}] have been successfully written. "
                    f"If creating these files was your assigned task, you are done. "
                    f"Respond with a brief summary and do NOT make further tool calls for these files.",
                }
            )
            _write_completion_signaled.update(new_writes)

        # Repetition / Infinite loop protection
        recent = _tool_call_history[-12:]
        repeat_counts = Counter(recent)
        looped = False
        for (name, path), count in repeat_counts.items():
            if count >= 5 and name in ("write_file", "patch_file") and path:
                ui_info(f"Loop detected: {name} on '{path}' called {count}x. Breaking.")
                looped = True
                messages.append(
                    {
                        "role": "system",
                        "content": f"LOOP DETECTED: You called {name} on '{path}' {count} times and it succeeded. Your task is complete. Do NOT make further tool calls. Summarize what was done.",
                    }
                )
                final_message = f"Completed writing {path}"
                break
        if looped:
            break
    else:
        ui_error(f"Reached max steps ({max_steps}). Stopping.")

    print(f"\n{dim('─' * 48)}")
    print(dim(f"  Tokens: {total_in} in / {total_out} out  ·  Steps: {step}  ·  Mode: {mode}"))
    if final_message:
        memory_save(f"Task: {user_message[:300]}\nOutcome: {final_message[:700]}", "session")

    try:
        session_messages = [
            m for m in messages if m.get("role") in ("user", "assistant", "tool")
        ]
        session_save(session_messages, mode, user_message)
    except Exception:
        pass

    return final_message
