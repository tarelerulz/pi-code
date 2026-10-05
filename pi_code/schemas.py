"""Tool definitions as the model sees them: lean schemas, the one-line
catalog, compact manuals, and schema-based argument repair.

The Pi 400 reads prompts at ~10 tokens/s, so the tool list is paid for in
seconds on every request: the full router list is ~10.4k chars (~2.6k
tokens), the weather tool alone ~3.5k. Measured 2026-10-04 with the quick
preset: a weather question took 2.5 min, 90% of it reading.
"""
import json
import re

from . import config
from .plugins import adapt_schema


def first_sentence(text, limit):
    text = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    text = m.group(1) if m else text
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def lean_tool(tool):
    """Same tool with first-sentence descriptions and no min/max/default noise."""
    tool = json.loads(json.dumps(tool))
    fn = tool["function"]
    fn["description"] = first_sentence(fn.get("description"), 160)
    params = fn.get("parameters") or {}
    for v in (params.get("properties") or {}).values():
        if "description" in v:
            v["description"] = first_sentence(v["description"], 90)
        for noise in ("minimum", "maximum", "default"):
            v.pop(noise, None)
    return adapt_schema(tool)


def sent_form(tool):
    """The tool as it is described to the model (full or lean)."""
    return tool if config.FULL_TOOL_DOCS else lean_tool(tool)


def catalog_line(tool):
    fn = sent_form(tool)["function"]
    params = fn.get("parameters") or {}
    req = set(params.get("required", []))
    args = ", ".join(k + ("" if k in req else "?") for k in (params.get("properties") or {}))
    return f"{fn['name']}({args}): {first_sentence(fn.get('description'), 90)}"


def tool_catalog(tools):
    """One line per tool: name(argument names) and a short description.
    The argument names let the model call simple tools right away instead of
    guessing them ("location" for location_name, 2026-10-04)."""
    lines = ["- " + catalog_line(t) for t in tools]
    return ("Tools (run one with call_tool; ? = optional argument; call load_tools "
            "first if you need a tool's full instructions):\n" + "\n".join(lines))


def resolve_tool_names(wanted, tools):
    """Exact names first; otherwise a unique-enough partial match ("weather")."""
    names = [t["function"]["name"] for t in tools]
    found = []
    for w in wanted if isinstance(wanted, list) else [wanted]:
        w = str(w).strip()
        if w in names:
            found.append(w)
            continue
        hits = [n for n in names if w.lower() in n.lower()]
        found += hits[:3]
    return list(dict.fromkeys(found))


def tool_manual(names, tools):
    """Compact manuals for these tools, as plain text for the conversation."""
    parts = []
    for t in tools:
        if t["function"]["name"] in names:
            fn = sent_form(t)["function"]
            parts.append(f"{fn['name']}: {fn.get('description', '')}\n"
                         f"arguments: {json.dumps(fn.get('parameters', {}).get('properties', {}))}")
    return "\n\n".join(parts)


def coerce_args(name, args, tools):
    """Numbers and booleans the model sent as strings ("days": "1") -> real
    types, going by the tool's schema; anything else is left alone."""
    tool = next((t for t in tools if t["function"]["name"] == name), None)
    props = ((tool or {}).get("function", {}).get("parameters") or {}).get("properties") or {}
    out = dict(args)
    for k, v in args.items():
        kind = (props.get(k) or {}).get("type")
        if not isinstance(v, str):
            continue
        try:
            if kind == "integer":
                out[k] = int(v.strip())
            elif kind == "number":
                f = float(v.strip())
                out[k] = int(f) if f.is_integer() else f
            elif kind == "boolean" and v.strip().lower() in ("true", "false"):
                out[k] = v.strip().lower() == "true"
        except ValueError:
            pass
    return out
