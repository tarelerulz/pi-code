"""Saved tool results.

A tool result bigger than config.TOOL_CHARS is written to
config.RESULTS_DIR/<id>.txt and the model sees only its start and end.
read_saved_result is handled here in pi-code (not by the router) so the model
can page through or search it.
"""
import os

from . import config

_saved = []     # paths, index = id - 1

READ_SAVED_TOOL = {"type": "function", "function": {
    "name": "read_saved_result",
    "description": "Read more of a long tool result that was shortened. "
                   "Give its id; either an offset (character position) to read "
                   "from, or 'find' to get only the lines containing that text.",
    "parameters": {"type": "object", "properties": {
        "id": {"type": "integer", "description": "result number shown in the shortened result"},
        "offset": {"type": "integer", "description": "character position to start at (default 0)"},
        "find": {"type": "string", "description": "only return lines containing this text"},
    }, "required": ["id"]}}}


def save_result(content):
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    path = os.path.join(config.RESULTS_DIR, f"{len(_saved) + 1}.txt")
    with open(path, "w") as f:
        f.write(content)
    _saved.append(path)
    return len(_saved)


def shorten_result(content):
    """What the model gets for a tool result: as-is, or start + end + a pointer."""
    limit = config.TOOL_CHARS
    if len(content) <= limit:
        return content
    rid = save_result(content)
    head, tail = content[:limit * 7 // 10], content[-(limit * 2 // 10):]
    return (f"{head}\n\n[... result #{rid} shortened: {len(content)} chars total, "
            f"middle left out. Call read_saved_result with id {rid} and an offset "
            f"or a 'find' text to see more ...]\n\n{tail}")


def read_saved(args):
    limit = config.TOOL_CHARS
    try:
        rid = int(args.get("id"))
        with open(_saved[rid - 1]) as f:
            text = f.read()
    except (TypeError, ValueError, IndexError, OSError):
        return f"no saved result {args.get('id')!r} (saved ids: 1-{len(_saved)})" if _saved \
            else "no saved results in this session"
    find = args.get("find")
    if find:
        hits = [f"{n}: {line}" for n, line in enumerate(text.splitlines(), 1) if find in line]
        out = "\n".join(hits) or f"no lines contain {find!r}"
        return out[:limit] + ("\n[... more matches cut ...]" if len(out) > limit else "")
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    part = text[offset:offset + limit]
    return f"[result #{rid}, chars {offset}-{offset + len(part)} of {len(text)}]\n{part}"
