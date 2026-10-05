"""Tool source: MCP servers started by pi-code itself (stdio transport).

Reads the same config format the llama.cpp router uses for
--mcp-servers-config: {"mcpServers": {"name": {"command", "args", "env"}}}
from PI_CODE_MCP_CONFIG (default ~/llama-mcp.json). Each server is started
once per pi-code session, spoken to with newline-delimited JSON-RPC
(initialize -> notifications/initialized -> tools/list / tools/call), and
stopped when pi-code exits. Tools are named <server>_<tool>, the same names
the router gives them, so adapters and picker labels keep working.
Server stderr goes to ~/.cache/pi-code/mcp-<server>.log.
"""
import atexit
import json
import os
import select
import subprocess
import time

CONFIG = os.path.expanduser(os.environ.get("PI_CODE_MCP_CONFIG", "~/llama-mcp.json"))
TIMEOUT = float(os.environ.get("PI_CODE_MCP_TIMEOUT", "120"))
PROTOCOL = "2025-06-18"
LOG_DIR = os.path.expanduser("~/.cache/pi-code")

_servers = {}      # server name -> Server
_tool_of = {}      # pi-code tool name -> (Server, MCP tool name)


class Server:
    def __init__(self, name, spec):
        self.name = name
        os.makedirs(LOG_DIR, exist_ok=True)
        self.log = open(os.path.join(LOG_DIR, f"mcp-{name}.log"), "w")
        env = dict(os.environ, **{k: str(v) for k, v in (spec.get("env") or {}).items()})
        self.proc = subprocess.Popen(
            [spec["command"]] + list(spec.get("args") or []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, env=env)
        self.buf = b""
        self.next_id = 1
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "pi-code", "version": "1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def _line(self, deadline):
        while b"\n" not in self.buf:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"MCP server {self.name} did not answer in {TIMEOUT:.0f} s")
            ready, _, _ = select.select([self.proc.stdout], [], [], left)
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError(f"MCP server {self.name} exited "
                                       f"(see {LOG_DIR}/mcp-{self.name}.log)")
                self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line

    def request(self, method, params):
        rid = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + TIMEOUT
        while True:
            line = self._line(deadline).strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue                      # stray non-JSON output
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", str(msg["error"])))
                return msg.get("result") or {}
            if "method" in msg and "id" in msg:   # server asks us something (ping...)
                self.send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    def tools(self):
        out, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            out += res.get("tools") or []
            cursor = res.get("nextCursor")
            if not cursor:
                return out

    def stop(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        self.log.close()


def _stop_all():
    for s in _servers.values():
        s.stop()


def list_tools():
    if not _servers:
        try:
            with open(CONFIG) as f:
                specs = json.load(f).get("mcpServers") or {}
        except (OSError, ValueError) as exc:
            print(f"pi-code: no MCP servers ({CONFIG}: {exc})")
            return []
        atexit.register(_stop_all)
        for name, spec in specs.items():
            try:
                _servers[name] = Server(name, spec)
            except Exception as exc:        # one broken server must not stop the rest
                print(f"pi-code: MCP server {name} failed to start: {exc}")
    tools = []
    _tool_of.clear()
    for name, server in _servers.items():
        try:
            listed = server.tools()
        except Exception as exc:
            print(f"pi-code: MCP server {name}: {exc}")
            continue
        for t in listed:
            full = f"{name}_{t['name']}"
            _tool_of[full] = (server, t["name"])
            tools.append({"type": "function", "function": {
                "name": full, "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
    return tools


def call(name, args):
    server, tool = _tool_of[name]
    res = server.request("tools/call", {"name": tool, "arguments": args})
    parts = []
    for c in res.get("content") or []:
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
        else:
            parts.append(f"[{c.get('type', 'unknown')} content]")
    text = "\n".join(parts)
    if not text and res.get("structuredContent") is not None:
        text = json.dumps(res["structuredContent"])
    return f"tool error: {text}" if res.get("isError") else text
