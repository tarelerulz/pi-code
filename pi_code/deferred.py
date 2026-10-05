"""Tool picking: send manuals only for the tools in use.

Deferred mode (default): the model sees a one-line catalog of tool names in
the system message and two fixed tools: load_tools (returns a tool's manual
as an ordinary tool result) and call_tool (runs a tool by name). The tool
list itself never changes, because chat templates put it at the very top:
changing it made the router re-read the whole prompt (measured 2026-10-04,
516 tokens = 50 s on the Pi). A picker (plug-in file) can add a "likely
tool" line to the question before the model starts.
"all" sends every manual up front (the old behaviour); -t NAMES sends
exactly those tools and skips all of this.
"""
from .results import READ_SAVED_TOOL, _saved
from .schemas import resolve_tool_names, sent_form, tool_manual

_tool_mode = ["deferred"]   # "deferred" or "all"; set in cli.main()
_loaded = set()             # tool names loaded this session

LOAD_TOOLS_TOOL = {"type": "function", "function": {
    "name": "load_tools",
    "description": "Get the instructions for tools from the tool list, by name. "
                   "Call this before call_tool.",
    "parameters": {"type": "object", "properties": {
        "names": {"type": "array", "items": {"type": "string"},
                  "description": "tool names from the list"},
    }, "required": ["names"]}}}

CALL_TOOL_TOOL = {"type": "function", "function": {
    "name": "call_tool",
    "description": "Run a tool from the tool list after loading its instructions.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "tool name"},
        "arguments": {"type": "object", "description": "the tool's arguments"},
    }, "required": ["name", "arguments"]}}}


def check_args(name, args, tools):
    """None if args fit the tool's schema, else a reply carrying its manual.

    In deferred mode the model may call a tool it never loaded and guess the
    argument names ("location" for location_name, seen 2026-10-04). Unknown or
    missing arguments -> don't run it; hand back the manual instead."""
    tool = next((t for t in tools if t["function"]["name"] == name), None)
    if tool is None:
        return None
    params = tool["function"].get("parameters") or {}
    props = params.get("properties") or {}
    unknown = [k for k in args if props and k not in props]
    missing = [k for k in params.get("required", []) if k not in args]
    if not unknown and not missing:
        return None
    _loaded.add(name)
    why = "; ".join(filter(None, [
        f"unknown argument(s): {', '.join(unknown)}" if unknown else "",
        f"missing: {', '.join(missing)}" if missing else ""]))
    return (f"Not run ({why}). Instructions:\n{tool_manual([name], tools)}\n\n"
            f"Call it again with call_tool and these argument names.")


def load_tools(args, tools):
    got = resolve_tool_names(args.get("names") or args.get("name") or [], tools)
    if not got:
        return ("No tool by that name. Pick names from this list: "
                + ", ".join(t["function"]["name"] for t in tools))
    _loaded.update(got)
    return (tool_manual(got, tools) +
            "\n\nRun it with call_tool, e.g. call_tool(name=\"" + got[0] + "\", arguments={...}).")


def tools_for_request(tools):
    """The tool schemas sent with one request."""
    if not tools:
        return []
    if _tool_mode[0] == "all":
        sent = [sent_form(t) for t in tools]
    else:
        sent = [LOAD_TOOLS_TOOL, CALL_TOOL_TOOL]
    if _saved:
        sent = sent + [READ_SAVED_TOOL]
    return sent
