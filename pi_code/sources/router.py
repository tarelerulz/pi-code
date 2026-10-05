"""Tool source: the llama.cpp router's /tools endpoint."""
import json
import urllib.error

from ..server import api


def list_tools():
    return [t["definition"] for t in api("/tools")]


def call(name, args):
    try:
        res = api("/tools", {"tool": name, "params": args})
    except urllib.error.HTTPError as e:
        return f"tool error: {e} {e.read().decode(errors='replace')[:300]}"
    except urllib.error.URLError as e:
        return f"tool error: {e}"
    return res.get("plain_text_response") or json.dumps(res)
