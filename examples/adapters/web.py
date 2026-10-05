"""pi-code adapter for web_search (pi-code-tools-mcp.py): ask before searching,
since the query leaves the machine; show the query."""
TOOLS = ["web_search"]
CONFIRM = True


def summary(name, args):
    return args.get("query", "")
