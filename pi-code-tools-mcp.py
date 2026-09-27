#!/usr/bin/env python3
"""Default MCP tool server shipped alongside pi-code.

A portable stdio MCP server (same JSON-RPC shape any llama.cpp router
--mcp-servers-config entry expects) providing one general-purpose tool:
web search. Ships with no personal/hardcoded paths, unlike a user's own
site-specific MCP servers -- point --mcp-servers-config at a JSON file
naming this script under the key "web" (see pi-code-mcp.json alongside
this file) and any router chat model, or pi-code itself, gets a
"web_search" tool with zero code changes anywhere else.

Needs the `ddgs` package importable by whatever Python runs this script
(`pip install ddgs`, or point DDGS_PATH at a venv's site-packages).
"""

import json
import os
import sys


DDGS_PATH = os.environ.get("DDGS_PATH")


def reply(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = {"code": -32000, "message": error}
    else:
        message["result"] = result
    print(json.dumps(message), flush=True)


def tool_list():
    return [
        {
            "name": "search",
            "description": "Search the web and return a few current results with URLs and snippets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "web search query"},
                },
                "required": ["query"],
            },
        },
    ]


def web_search(query):
    if DDGS_PATH and DDGS_PATH not in sys.path:
        sys.path.insert(0, DDGS_PATH)
    from ddgs import DDGS
    hits = list(DDGS().text(query, max_results=5))
    if not hits:
        return "no web results"
    lines = []
    for hit in hits:
        title = hit.get("title", "").strip()
        url = hit.get("href", "").strip()
        snippet = hit.get("body", "").strip()[:400]
        lines.append(f"- {title}\n  {url}\n  {snippet}")
    return "\n".join(lines)


def call_tool(name, arguments):
    if name == "search":
        query = (arguments.get("query") or "").strip()
        if not query:
            return {"content": [{"type": "text", "text": "no query given"}], "isError": True}
        try:
            text = web_search(query)
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"web search failed: {exc}"}], "isError": True}
        return {"content": [{"type": "text", "text": text}]}
    raise ValueError(f"unknown tool: {name}")


def handle(request):
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        reply(request_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pi-code-tools", "version": "0.1"},
        })
    elif method == "notifications/initialized":
        return
    elif method == "ping":
        reply(request_id, {})
    elif method == "tools/list":
        reply(request_id, {"tools": tool_list()})
    elif method == "tools/call":
        try:
            params = request.get("params", {})
            reply(request_id, call_tool(params.get("name", ""), params.get("arguments", {})))
        except Exception as exc:
            reply(request_id, error=str(exc))
    elif request_id is not None:
        reply(request_id, error=f"unsupported method: {method}")


def main():
    for line in sys.stdin:
        try:
            handle(json.loads(line))
        except Exception as exc:
            print(f"MCP request error: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
