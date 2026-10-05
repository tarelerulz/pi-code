"""Tool sources: where pi-code's tools come from.

  router  the llama.cpp router's /tools endpoint (its built-in file/shell
          tools plus the MCP servers in its --mcp-servers-config). Default.
  mcp     pi-code starts the MCP servers itself (stdio, same config file
          format as the router: PI_CODE_MCP_CONFIG, default ~/llama-mcp.json)
          and names their tools <server>_<tool> like the router does.
  local   pi-code's own file and shell tools (read_file, write_file,
          edit_file, exec_shell_command, file_glob_search, grep_search,
          get_info), same names and schemas as the router's built-ins.

Pick with PI_CODE_TOOL_SOURCES or --sources, comma-separated, e.g.
"mcp,local" = no router tools at all (the router still runs the models).
When two sources offer the same tool name, the first one listed wins.

A source is a module with list_tools() -> [tool definitions] and
call(name, args) -> text. Besides the three built in here, any NAME.py in
~/.config/pi-code/sources/ (PI_CODE_SOURCES_DIR) defining both is a source
called NAME; a file there named like a built-in one replaces it.
"""
import importlib
import os

from .. import config
from ..plugins import _load_plugins

BUILT_IN = ("router", "mcp", "local")
PLUGINS = {name: mod for name, mod in _load_plugins(config.SOURCES_DIR)
           if callable(getattr(mod, "list_tools", None)) and callable(getattr(mod, "call", None))}
KNOWN = tuple(dict.fromkeys(BUILT_IN + tuple(sorted(PLUGINS))))
DEFAULT = os.environ.get("PI_CODE_TOOL_SOURCES", "router")

_owner = {}        # tool name -> source module
_active = []       # source modules in use


def use(spec):
    """Select sources from "a,b"; returns their names. Unknown names raise ValueError."""
    names = [n.strip() for n in spec.split(",") if n.strip()]
    bad = [n for n in names if n not in KNOWN]
    if bad or not names:
        raise ValueError(f"unknown tool source(s): {', '.join(bad) or '(none)'}; "
                         f"choose from {', '.join(KNOWN)}")
    _active[:] = [PLUGINS[n] if n in PLUGINS else importlib.import_module(f"{__name__}.{n}")
                  for n in names]
    _owner.clear()
    return names


def list_tools():
    """All tool definitions from the active sources, first source wins on a name clash."""
    if not _active:
        use(DEFAULT)
    tools = []
    _owner.clear()
    for src in _active:
        for t in src.list_tools():
            name = t["function"]["name"]
            if name not in _owner:
                _owner[name] = src
                tools.append(t)
    return tools


def call(name, args):
    """Run a tool; always returns text (errors as "tool error: ...")."""
    src = _owner.get(name)
    if src is None:
        return f"tool error: no source offers {name!r}"
    try:
        return src.call(name, args)
    except Exception as exc:          # a failing tool must not end the session
        return f"tool error: {exc}"
