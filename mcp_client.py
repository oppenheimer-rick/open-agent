#!/usr/bin/env python3
"""
Model Context Protocol (MCP) client for open-agent.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Discovers MCP servers, connects to them via stdio, lists available tools,
and registers them into the agent's TOOL_MAP and TOOLS list.

MCP servers are configured in ~/.agentic-loop/mcp_config.json:

    {
      "servers": [
        {
          "name": "filesystem",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
          "enabled": true
        },
        {
          "name": "github",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-github"],
          "env": {"GITHUB_TOKEN": "ghp_..."},
          "enabled": true
        }
      ]
    }

Reference: https://modelcontextprotocol.io
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

MCP_CONFIG_PATH = Path.home() / ".agentic-loop" / "mcp_config.json"
MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── JSON-RPC helpers ────────────────────────────────────────────────────────────


def _json_rpc_request(method: str, params: dict | None = None) -> str:
    """Build a JSON-RPC 2.0 request string."""
    req = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex[:8],
        "method": method,
    }
    if params is not None:
        req["params"] = params
    return json.dumps(req) + "\n"


def _parse_json_rpc_response(raw: str) -> dict:
    """Parse a JSON-RPC response, handling potential newline framing."""
    lines = raw.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


# ── MCP Server (Process Manager) ────────────────────────────────────────────────


class MCPServer:
    """Manages a single MCP server process via stdio transport."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._process: subprocess.Popen | None = None
        self._tools: list[dict] = []
        self._buffer = ""

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """Start the MCP server subprocess."""
        if self.is_running:
            return True

        cmd = [self.command] + self.args
        full_env = os.environ.copy()
        full_env.update(self.env)

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                text=True,
                bufsize=1,  # line-buffered
            )
            return True
        except FileNotFoundError:
            print(f"  ⚠ MCP '{self.name}': command not found: {self.command}")
            return False
        except Exception as e:
            print(f"  ⚠ MCP '{self.name}': failed to start: {e}")
            return False

    def stop(self):
        """Stop the MCP server process."""
        if self._process and self._process.poll() is None:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def _write(self, text: str):
        """Write to the process stdin."""
        if self._process and self._process.stdin:
            self._process.stdin.write(text)
            self._process.stdin.flush()

    def _read(self, timeout: float = 5.0) -> str:
        """Read from the process stdout until we get a complete JSON response."""
        if not self._process or not self._process.stdout:
            return ""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._process.stdout.readline()
            if not line:
                break
            self._buffer += line
            # Check if we have a complete JSON object
            try:
                json.loads(line.strip())
                result = self._buffer
                self._buffer = ""
                return result
            except json.JSONDecodeError:
                continue

        return ""

    def _call(self, method: str, params: dict | None = None) -> dict | None:
        """Send a JSON-RPC request and wait for the response."""
        if not self.is_running:
            return None

        req = _json_rpc_request(method, params)
        self._write(req)
        raw = self._read(timeout=10.0)

        if not raw:
            return None

        resp = _parse_json_rpc_response(raw)
        if "error" in resp:
            error = resp["error"]
            print(f"  ⚠ MCP '{self.name}' {method} error: {error.get('message', error)}")
            return None

        return resp.get("result")

    def initialize(self) -> bool:
        """Initialize the MCP session (required handshake)."""
        result = self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "open-agent",
                "version": "2.2.0",
            },
        })
        if result is None:
            return False

        # Send initialized notification (no response expected)
        if self._process and self._process.stdin:
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            self._process.stdin.write(notif)
            self._process.stdin.flush()

        return True

    def list_tools(self) -> list[dict]:
        """Discover tools from this MCP server.
        Returns list of tool definitions in MCP format:
        {name, description, inputSchema}
        """
        if not self.is_running:
            if not self.start():
                return []
            if not self.initialize():
                return []

        result = self._call("tools/list")
        if result is None:
            return []

        self._tools = result.get("tools", [])
        return self._tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool on the MCP server."""
        result = self._call("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if result is None:
            return f"ERROR: MCP tool '{tool_name}' failed or returned no result"

        # MCP tool results have a 'content' array with text/image/resource items
        content = result.get("content", [])
        output_parts = []
        for item in content:
            item_type = item.get("type", "text")
            if item_type == "text":
                output_parts.append(item.get("text", ""))
            elif item_type == "resource":
                resource = item.get("resource", {})
                text = resource.get("text", "")
                blob = resource.get("blob", "")
                output_parts.append(text or f"[binary resource: {len(blob)} bytes]")

        is_error = result.get("isError", False)
        output = "\n".join(output_parts)
        if is_error:
            return f"ERROR: {output}"
        return output

    def __del__(self):
        self.stop()


# ── MCP Manager ────────────────────────────────────────────────────────────────


class MCPManager:
    """Discovers MCP servers and integrates them into the agent's tool ecosystem."""

    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else MCP_CONFIG_PATH
        self.servers: list[MCPServer] = []
        self._registered_tools: list[dict] = []
        self._registered_handlers: dict[str, callable] = {}

    def load_config(self) -> list[dict]:
        """Load MCP server configurations from the config file."""
        if not self.config_path.exists():
            return []

        try:
            data = json.loads(self.config_path.read_text())
            return [s for s in data.get("servers", []) if s.get("enabled", True)]
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠ MCP config error: {e}")
            return []

    def save_default_config(self):
        """Create a default MCP config if none exists."""
        if not self.config_path.exists():
            default = {
                "servers": [
                    {
                        "name": "fetch",
                        "description": "Scrapes and fetches web pages cleanly, converting them to markdown",
                        "command": "npx",
                        "args": [
                            "-y",
                            "@modelcontextprotocol/server-fetch"
                        ],
                        "enabled": True,
                    },
                    {
                        "name": "filesystem",
                        "description": "Advanced filesystem operations (search, permissions, metadata)",
                        "command": "npx",
                        "args": [
                            "-y",
                            "@modelcontextprotocol/server-filesystem",
                            os.getcwd(),
                        ],
                        "enabled": False,
                    },
                    {
                        "name": "git",
                        "description": "Git operations (log, diff, blame, branches)",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-git"],
                        "enabled": False,
                    },
                ]
            }
            self.config_path.write_text(json.dumps(default, indent=2))
            print(f"  ✓ Created default MCP config: {self.config_path}")
            print("    Edit this file to enable MCP servers, then call /mcp")

    def connect_all(self) -> tuple[list[dict], dict[str, callable]]:
        """Connect to all enabled MCP servers and discover their tools.

        Returns:
            (openai_tools, tool_map) — ready to inject into TOOLS and TOOL_MAP
        """
        configs = self.load_config()
        if not configs:
            return [], {}

        openai_tools = []
        tool_map = {}

        for cfg in configs:
            name = cfg.get("name", "unknown")
            command = cfg.get("command", "")
            args = cfg.get("args", [])
            env = cfg.get("env", {})

            server = MCPServer(name=name, command=command, args=args, env=env)
            self.servers.append(server)

            tools = server.list_tools()
            if not tools:
                continue

            print(f"  ✓ MCP '{name}': {len(tools)} tools discovered")

            for tool in tools:
                tool_name = tool.get("name", "")
                tool_desc = tool.get("description", f"MCP tool from {name}")
                schema = tool.get("inputSchema", {"type": "object", "properties": {}})

                # Build OpenAI-compatible tool definition
                openai_def = {
                    "type": "function",
                    "function": {
                        "name": f"mcp_{name}_{tool_name}",
                        "description": f"[MCP:{name}] {tool_desc}",
                        "parameters": schema,
                    },
                }

                # Build handler with closure
                def _make_handler(srv=server, t_name=tool_name):
                    def handler(args: dict) -> str:
                        return srv.call_tool(t_name, args)
                    return handler

                openai_tools.append(openai_def)
                tool_map[f"mcp_{name}_{tool_name}"] = _make_handler()
                self._registered_tools.append(openai_def)
                self._registered_handlers[f"mcp_{name}_{tool_name}"] = tool_map[f"mcp_{name}_{tool_name}"]

        return openai_tools, tool_map

    def disconnect_all(self):
        """Stop all MCP server processes and clear registered tools."""
        for server in self.servers:
            server.stop()
        self.servers.clear()
        self._registered_tools.clear()
        self._registered_handlers.clear()

    def get_summary(self) -> str:
        """Return a summary of MCP status for display."""
        if not self.servers:
            return "  📡 MCP: No servers configured"

        lines = ["  📡 MCP Servers:"]
        for s in self.servers:
            status = "✓ online" if s.is_running else "✗ offline"
            tools = len(s._tools)
            lines.append(f"     • {s.name}: {status} ({tools} tools)")
        return "\n".join(lines)


# ── Module-level singleton ─────────────────────────────────────────────────────

_manager: MCPManager | None = None


def get_manager() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


def connect_and_register() -> tuple[list[dict], dict[str, callable]]:
    """Convenience: connect all MCP servers and get tools+handlers.
    Returns ([openai_tools], {name: handler}) for injecting into the agent."""
    mgr = get_manager()
    return mgr.connect_all()


def disconnect():
    """Disconnect all MCP servers."""
    mgr = get_manager()
    mgr.disconnect_all()


def init_config():
    """Create default MCP config if it doesn't exist."""
    mgr = get_manager()
    mgr.save_default_config()


def status() -> str:
    """Get MCP status string."""
    mgr = get_manager()
    return mgr.get_summary()


# ── Integration helpers for loop.py ─────────────────────────────────────────────

def inject_into_agent(TOOLS: list, TOOL_MAP: dict) -> tuple[list, dict]:
    """Inject MCP tools into the agent's TOOLS and TOOL_MAP.

    Call this at startup to register MCP tools. Returns the modified
    (TOOLS, TOOL_MAP) with MCP tools appended.
    """
    mcp_tools, mcp_handlers = connect_and_register()
    if mcp_tools:
        TOOLS.extend(mcp_tools)
        TOOL_MAP.update(mcp_handlers)
    return TOOLS, TOOL_MAP


if __name__ == "__main__":
    # CLI test: connect and list tools
    init_config()

    tools, handlers = connect_and_register()
    if tools:
        print(f"Registered {len(tools)} MCP tools:")
        for t in tools:
            fn = t["function"]
            print(f"  • {fn['name']}: {fn['description'][:80]}")
    else:
        print("No MCP tools found. Edit config at:", MCP_CONFIG_PATH)
        print("Then run with: python -m mcp_client")
