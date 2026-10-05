"""pi-code adapter for ~/llama-tools-mcp.py's tools: notes search and one-shot
dictation only read, so they run without asking."""
TOOLS = ["local_search_notes", "local_dictate_once"]
CONFIRM = False


def summary(name, args):
    return args.get("query") if name == "local_search_notes" else None
