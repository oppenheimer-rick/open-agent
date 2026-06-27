#!/usr/bin/env python3
"""
Provider abstraction for open-agent.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Supports:
  - OpenAI-compatible endpoints (llama.cpp, vLLM, LiteLLM, OpenAI, OpenRouter, Groq, etc.)
  - Ollama native API (for Ollama < 0.3.0 without /v1/chat/completions)
  - Anthropic (Claude)

Configuration via environment variables:
  LLM_PROVIDER    - "openai" (default), "ollama", "anthropic", "openrouter"
  LLM_BASE        - Base URL for the API (default: http://localhost:8083/v1)
  LLM_API_KEY     - API key (for OpenAI, OpenRouter, Anthropic)
  LLM_MODEL       - Model name override (default: auto-detected)
  LLM_MAX_TOKENS  - Max generation tokens (default: 4096)
  LLM_TEMPERATURE - Temperature (default: 0.6)
"""

import json
import os
import time
import httpx
from typing import Any

# ── Configuration ───────────────────────────────────────────────────────────────

PROVIDER = os.environ.get("LLM_PROVIDER", "openai").lower().strip()
BASE_URL = os.environ.get("LLM_BASE", "http://localhost:8083/v1").rstrip("/")
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "")
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "4096"))
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.6"))

# Auto-detect provider from base URL if not explicitly set
if PROVIDER == "openai" and not API_KEY:
    # Check if it looks like a known provider
    if "openrouter" in BASE_URL.lower():
        PROVIDER_ACTUAL = "openrouter"
    elif "api.openai.com" in BASE_URL.lower():
        PROVIDER_ACTUAL = "openai"
    elif "api.groq.com" in BASE_URL.lower():
        PROVIDER_ACTUAL = "groq"
    elif "ollama" in BASE_URL.lower() or "11434" in BASE_URL:
        PROVIDER_ACTUAL = "ollama"
    elif "8083" in BASE_URL or "127.0.0.1" in BASE_URL or "localhost" in BASE_URL:
        PROVIDER_ACTUAL = "llamacpp"
    else:
        PROVIDER_ACTUAL = "openai"  # assume local llama.cpp/vLLM
else:
    PROVIDER_ACTUAL = PROVIDER

# Provider-specific model defaults
PROVIDER_DEFAULTS = {
    "openai": MODEL or "gpt-4o-mini",
    "openrouter": MODEL or "openrouter/auto",
    "ollama": MODEL or "llama3.2",
    "anthropic": MODEL or "claude-sonnet-4",
    "groq": MODEL or "llama-3.3-70b-versatile",
    "llamacpp": MODEL or "local",
}


def get_chat_url() -> str:
    """Get the chat completions URL for the current provider."""
    if PROVIDER_ACTUAL == "anthropic":
        return "https://api.anthropic.com/v1/messages"
    elif PROVIDER_ACTUAL == "ollama" and not BASE_URL.endswith("/v1"):
        # Ollama native API
        return BASE_URL.rstrip("/") + "/api/chat"
    return BASE_URL + "/chat/completions"


def get_headers() -> dict:
    """Get HTTP headers for the current provider."""
    headers = {"User-Agent": "open-agent/2.2"}
    if PROVIDER_ACTUAL == "openai":
        if API_KEY:
            headers["Authorization"] = f"Bearer {API_KEY}"
    elif PROVIDER_ACTUAL == "openrouter":
        headers["Authorization"] = f"Bearer {API_KEY or os.environ.get('OPENROUTER_API_KEY', '')}"
        headers["HTTP-Referer"] = os.environ.get("OPENROUTER_REFERER", "https://github.com/oppenheimer-rick/open-agent")
    elif PROVIDER_ACTUAL == "anthropic":
        headers["x-api-key"] = API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
        headers["anthropic-version"] = "2023-06-01"
        headers["Content-Type"] = "application/json"
    elif PROVIDER_ACTUAL == "groq":
        headers["Authorization"] = f"Bearer {API_KEY or os.environ.get('GROQ_API_KEY', '')}"
    return headers


def get_model() -> str:
    """Return the effective model name."""
    return MODEL or PROVIDER_DEFAULTS.get(PROVIDER_ACTUAL, "local")


def get_temperature() -> float:
    return TEMPERATURE


def get_max_tokens() -> int:
    return MAX_TOKENS


# ── Streaming Chat (OpenAI-compatible SSE) ─────────────────────────────────────

def iter_chat_events(
    messages: list,
    tools: list | None = None,
):
    """
    Stream chat completions from the configured provider.
    Yields structured events compatible with the ConsoleRenderer.
    
    Event types:
      llm_start        → label
      llm_first_output → (no data, signals first token received)
      reasoning_delta  → text
      assistant_delta  → text
      tool_call_delta  → index, name, arguments
      assistant_done   → message, finish_reason, usage
      error            → message
    """
    payload: dict[str, Any] = {
        "model": get_model(),
        "messages": messages,
        "temperature": get_temperature(),
        "max_tokens": get_max_tokens(),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    yield {"type": "llm_start", "label": "Thinking…"}
    first_token = True

    full_content = ""
    tool_calls_acc: dict[int, dict] = {}
    finish_reason = "stop"
    usage: dict = {}

    url = get_chat_url()
    headers = get_headers()
    model_name = get_model()

    try:
        with httpx.Client(timeout=180) as client:
            if PROVIDER_ACTUAL == "anthropic":
                yield from _stream_anthropic(client, url, headers, model_name, messages, tools, payload)
                return

            with client.stream(
                "POST", url, json=payload, headers=headers
            ) as resp:
                resp.raise_for_status()

                # Handle Ollama native format separately
                if PROVIDER_ACTUAL == "ollama" and "v1" not in url:
                    yield from _stream_ollama_native(
                        resp, first_token, full_content, tool_calls_acc, finish_reason, usage
                    )
                    return

                for raw_line in resp.iter_lines():
                    if not raw_line.startswith("data: "):
                        continue
                    data = raw_line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    choice = choices[0] if choices else {}
                    delta = choice.get("delta", {})
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = fr

                    # Reasoning/thinking token (multi-server compatible)
                    reasoning = (
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or delta.get("thinking")
                    )

                    # Stop spinner on first output
                    if first_token and (
                        delta.get("content") or reasoning or delta.get("tool_calls")
                    ):
                        yield {"type": "llm_first_output"}
                        first_token = False

                    if reasoning:
                        yield {"type": "reasoning_delta", "text": reasoning}

                    # Text token
                    token = delta.get("content") or ""
                    if token:
                        full_content += token
                        yield {"type": "assistant_delta", "text": token}

                    # Tool call delta accumulation
                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        entry = tool_calls_acc[idx]

                        if tc_delta.get("id"):
                            entry["id"] = tc_delta["id"]
                        fn = tc_delta.get("function", {})
                        if fn.get("name"):
                            delta_name = fn["name"]
                            entry["name"] += delta_name
                        if fn.get("arguments"):
                            delta_args = fn["arguments"]
                            entry["arguments"] += delta_args

                        yield {
                            "type": "tool_call_delta",
                            "index": idx,
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", ""),
                        }

                    if "usage" in chunk:
                        usage = chunk["usage"]

    except httpx.HTTPStatusError as e:
        yield {
            "type": "error",
            "message": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
        raise
    except httpx.ConnectError:
        yield {
            "type": "error",
            "message": f"Cannot connect to {PROVIDER_ACTUAL} at {url} — is it running?",
        }
        raise
    except KeyboardInterrupt:
        yield {"type": "error", "message": "Interrupted by user."}
        raise

    # Assemble tool_calls list
    tool_calls_list = []
    for idx in sorted(tool_calls_acc):
        tc = tool_calls_acc[idx]
        if tc["name"]:
            tool_calls_list.append({
                "id": tc["id"] or f"call_{idx}_{int(time.time())}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"] or "{}",
                },
            })

    msg = {"role": "assistant", "content": full_content or None}
    if tool_calls_list:
        msg["tool_calls"] = tool_calls_list

    yield {
        "type": "assistant_done",
        "message": msg,
        "finish_reason": finish_reason,
        "usage": usage,
    }


def _stream_anthropic(client, url, headers, model, messages, tools, payload):
    """Handle Anthropic-specific API format."""
    # Convert OpenAI messages to Anthropic format
    system_msg = ""
    anthropic_messages = []
    for m in messages:
        if m["role"] == "system":
            system_msg = m.get("content", "")
        elif m["role"] in ("user", "assistant"):
            anthropic_messages.append({
                "role": m["role"],
                "content": m.get("content", ""),
            })
        elif m["role"] == "tool":
            # Anthropic uses tool_result content blocks
            anthropic_messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }
                ],
            })

    # Convert tool definitions to Anthropic format
    anthropic_tools = None
    if tools:
        anthropic_tools = []
        for t in tools:
            fn = t.get("function", {})
            anthropic_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

    ant_payload = {
        "model": model,
        "max_tokens": get_max_tokens(),
        "messages": anthropic_messages,
        "stream": True,
    }
    if system_msg:
        ant_payload["system"] = system_msg
    if anthropic_tools:
        ant_payload["tools"] = anthropic_tools

    yield {"type": "llm_start", "label": "Thinking…"}
    first_token = True
    full_content = ""
    finish_reason = "stop"
    usage = {}
    current_tool_name = ""
    current_tool_input = ""
    tool_calls_list = []

    try:
        with client.stream("POST", url, json=ant_payload, headers=headers) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line.startswith("data: "):
                    continue
                data = raw_line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                chunk_type = chunk.get("type", "")

                if first_token and chunk_type in ("content_block_start", "content_block_delta", "message_start"):
                    yield {"type": "llm_first_output"}
                    first_token = False

                if chunk_type == "content_block_start":
                    block = chunk.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool_name = block.get("name", "")
                        current_tool_input = ""

                elif chunk_type == "content_block_delta":
                    delta = chunk.get("delta", {})
                    if delta.get("type") == "text_delta":
                        token = delta.get("text", "")
                        full_content += token
                        yield {"type": "assistant_delta", "text": token}
                    elif delta.get("type") == "input_json_delta":
                        current_tool_input += delta.get("partial_json", "")

                elif chunk_type == "message_delta":
                    delta = chunk.get("delta", {})
                    if delta.get("stop_reason"):
                        finish_reason = delta["stop_reason"]
                    usage = chunk.get("usage", usage)

                elif chunk_type == "content_block_stop":
                    if current_tool_name:
                        try:
                            parsed_args = json.loads(current_tool_input or "{}")
                        except json.JSONDecodeError:
                            parsed_args = {}
                        tool_calls_list.append({
                            "id": f"toolu_{int(time.time())}",
                            "type": "function",
                            "function": {
                                "name": current_tool_name,
                                "arguments": json.dumps(parsed_args),
                            },
                        })
                        current_tool_name = ""
                        current_tool_input = ""

    except httpx.HTTPStatusError as e:
        yield {"type": "error", "message": f"Anthropic HTTP {e.response.status_code}: {e.response.text[:200]}"}
        raise
    except KeyboardInterrupt:
        yield {"type": "error", "message": "Interrupted by user."}
        raise

    msg = {"role": "assistant", "content": full_content or None}
    if tool_calls_list:
        msg["tool_calls"] = tool_calls_list

    yield {
        "type": "assistant_done",
        "message": msg,
        "finish_reason": finish_reason,
        "usage": usage,
    }


def _stream_ollama_native(resp, first_token, full_content, tool_calls_acc, finish_reason, usage):
    """Handle Ollama's native API streaming format (for Ollama < 0.3.0)."""
    first_token_flag = [first_token]
    full = [full_content]
    tc_acc = tool_calls_acc
    fr = [finish_reason]
    usg = usage

    for raw_line in resp.iter_lines():
        if not raw_line.strip():
            continue
        try:
            chunk = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if first_token_flag[0] and chunk.get("message"):
            yield {"type": "llm_first_output"}
            first_token_flag[0] = False

        message = chunk.get("message", {})
        token = message.get("content", "")

        if token:
            full[0] += token
            yield {"type": "assistant_delta", "text": token}

        if chunk.get("done"):
            fr[0] = "stop"
            break

        if "tool_calls" in chunk:
            # Ollama tool call format
            tc = chunk["tool_calls"]
            for i, call in enumerate(tc):
                fn = call.get("function", {})
                tc_acc[i] = {
                    "id": f"ollama_call_{i}_{int(time.time())}",
                    "name": fn.get("name", ""),
                    "arguments": json.dumps(fn.get("arguments", {})),
                }

    # Reconstruct final message
    tool_calls_list = []
    for idx in sorted(tc_acc):
        tc_entry = tc_acc[idx]
        if tc_entry["name"]:
            tool_calls_list.append({
                "id": tc_entry["id"],
                "type": "function",
                "function": {
                    "name": tc_entry["name"],
                    "arguments": tc_entry["arguments"] or "{}",
                },
            })

    msg = {"role": "assistant", "content": full[0] or None}
    if tool_calls_list:
        msg["tool_calls"] = tool_calls_list

    yield {
        "type": "assistant_done",
        "message": msg,
        "finish_reason": fr[0],
        "usage": usg,
    }


# ── Synchronous (non-streaming) generation ─────────────────────────────────────

def generate(system_prompt: str, user_prompt: str, max_tokens: int = 512) -> str:
    """Simple synchronous LLM call for lightweight sub-tasks (memory, OOTB, etc.)."""
    payload = {
        "model": get_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }
    url = get_chat_url()
    headers = get_headers()

    try:
        with httpx.Client(timeout=300.0) as client:
            if PROVIDER_ACTUAL == "anthropic":
                return _anthropic_generate(client, url, headers, system_prompt, user_prompt, max_tokens, payload)

            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Error generating: {e}"


def _anthropic_generate(client, url, headers, system_prompt, user_prompt, max_tokens, payload):
    """Anthropic non-streaming generation."""
    ant_payload = {
        "model": get_model(),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if system_prompt:
        ant_payload["system"] = system_prompt

    resp = client.post(url, json=ant_payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"].strip()


def quick_call(prompt: str, max_tokens: int = 200) -> str:
    """Minimal synchronous call for quick sub-tasks."""
    try:
        payload = {
            "model": get_model(),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": False,
        }
        url = get_chat_url()
        headers = get_headers()
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if PROVIDER_ACTUAL == "anthropic":
                return data["content"][0]["text"].strip()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        return f"[LLM call failed: {e}]"


def status_line() -> str:
    """Return a compact status line for the REPL toolbar."""
    model_name = get_model()
    provider_name = PROVIDER_ACTUAL.upper()
    return f"{provider_name} · {model_name}"
