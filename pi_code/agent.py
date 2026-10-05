"""The agent loop: one user turn = model <-> tools until a plain answer."""
import json
import os
import time
import urllib.error

from . import config
from .deferred import _tool_mode, check_args, load_tools, tools_for_request
from .plugins import PICKERS, condense_result, fix_args, needs_confirm, tool_summary
from .results import _saved, read_saved, shorten_result
from .schemas import catalog_line, coerce_args, resolve_tool_names, tool_catalog
from . import sources
from .server import call_model
from .ui import bold, dim

SYSTEM_PROMPT = (
    "You are a local coding agent. When asked to create or fix a program, "
    "use your tools: write the file, compile it if applicable, run it, and "
    "if there are errors, fix them and retry. Report the real output. If a "
    "search tool is available, use it for current information, "
    "documentation, or facts you are not sure about. Only use tools when "
    "the task needs files, commands, or current information; answer stable "
    "general knowledge questions directly. Be concise."
)

# Used instead of SYSTEM_PROMPT whenever tools are off (-T, or /tools
# toggled off): SYSTEM_PROMPT tells the model to call tools that don't
# actually exist in that request, which small models can hallucinate as
# literal fake tool-call text.
PLAIN_SYSTEM_PROMPT = (
    "You are a helpful assistant running locally. Answer directly and "
    "concisely."
)

_last_model = [None]


def _prompt(override, filename, default):
    """Setting text, else PROMPTS_DIR/filename, else the built-in default."""
    if override:
        return override
    try:
        with open(os.path.join(config.PROMPTS_DIR, filename)) as f:
            text = f.read().strip()
        if text:
            return text
    except OSError:
        pass
    return default


def system_prompt():
    return _prompt(config.SYSTEM_PROMPT, "system.txt", SYSTEM_PROMPT)


def plain_prompt():
    return _prompt(config.PLAIN_PROMPT, "plain.txt", PLAIN_SYSTEM_PROMPT)


def system_msg(tools):
    """System message for this tool list ([] = plain chat). In deferred mode
    it carries the one-line tool catalog, so it stays the same from request
    to request and the router can keep it cached."""
    if not tools:
        return {"role": "system", "content": plain_prompt()}
    content = system_prompt()
    if _tool_mode[0] == "deferred":
        content += "\n\n" + tool_catalog(tools)
    return {"role": "system", "content": content}


def last_question(msgs):
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(
                p.get("text", "") for p in c if p.get("type") == "text")
    return ""


def run_turn(msgs, model, tools, auto_yes):
    """One user turn: loop model <-> tools until a final text answer."""
    always = auto_yes
    if tools and _tool_mode[0] == "deferred":
        # a picker that isn't installed (no plug-in file) quietly means "none"
        name = config.TOOL_PICKER if config.TOOL_PICKER in PICKERS else "none"
        t0 = time.time()
        guess = resolve_tool_names(PICKERS[name](last_question(msgs), tools) or [], tools)
        if name != "none":
            print(dim(f"  (picker {name}: {', '.join(guess) or 'no tool'}, "
                      f"{time.time() - t0:.1f}s)"))
        if guess and isinstance(msgs[-1].get("content"), str):
            # one catalog line, not the manual: every word costs ~0.1 s to read
            lines = [catalog_line(t) for t in tools if t["function"]["name"] in guess]
            msgs[-1]["content"] += "\n\n(Likely tool, run with call_tool: " + "; ".join(lines) + ")"
    for rnd in range(config.MAX_ROUNDS):
        t0 = time.time()
        hint = ""
        if model != _last_model[0]:
            hint = " (loading the model, this takes a few minutes)"
            _last_model[0] = model
        print(dim(f"  … {model} thinking{hint}"), flush=True)
        try:
            m, finish, timings = call_model(msgs, model, tools_for_request(tools))
        except (urllib.error.URLError, RuntimeError, ValueError) as e:
            print(f"server error: {e}")
            return
        msgs.append(m)
        dt = time.time() - t0
        speed = ""
        if timings and timings.get("predicted_per_second"):
            speed = f", {timings['predicted_per_second']:.1f} tok/s"
        cut_off = finish == "length"

        if not m.get("tool_calls"):
            if not m["content"]:
                print("\n(no reply)")
            if cut_off:
                print(bold(f"  [reply cut off at the {config.MAX_TOKENS}-token limit — "
                           f"say \"continue\", or set PI_CODE_MAX_TOKENS higher]"))
            print(dim(f"  [{dt:.0f}s{speed}]"))
            return

        if cut_off:
            # arguments of the last call are probably incomplete JSON
            print(bold(f"  [tool call cut off at the {config.MAX_TOKENS}-token limit — not run; "
                       f"ask for a smaller step, or set PI_CODE_MAX_TOKENS higher]"))
            for tc in m["tool_calls"]:
                msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": "Not run: this tool call was cut off by the reply length limit."})
            return

        for tc in m["tool_calls"]:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "call_tool":
                inner = args.get("arguments") or {}
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except json.JSONDecodeError:
                        inner = {}
                real = resolve_tool_names(args.get("name") or "", tools)
                name, args = (real[0] if real else str(args.get("name"))), inner
            args = coerce_args(name, fix_args(name, args), tools)
            print(f"  {bold(name)}  {tool_summary(name, args)}  {dim(f'[{dt:.0f}s{speed}]')}")

            skip = False
            if needs_confirm(name) and not always:
                try:
                    ans = input("    run? [Y/n/a(lways)] ").strip().lower()
                except EOFError:
                    ans = "n"
                if ans == "a":
                    always = True
                elif ans == "n":
                    skip = True

            if skip:
                content = "User declined to run this tool."
            elif name == "read_saved_result":
                content = read_saved(args)
            elif name == "load_tools":
                content = load_tools(args, tools)
            elif tools and name not in {t["function"]["name"] for t in tools}:
                content = (f"Unknown tool {name!r}. Tools: "
                           + ", ".join(t["function"]["name"] for t in tools))
            elif _tool_mode[0] == "deferred" and check_args(name, args, tools):
                content = check_args(name, args, tools)
            else:
                content = condense_result(name, sources.call(name, args))
            shown = content.strip()
            if shown:
                trimmed = shown[:400] + ("…" if len(shown) > 400 else "")
                print(dim("    " + trimmed.replace("\n", "\n    ")))
            if name not in ("read_saved_result", "load_tools"):
                full, content = content, shorten_result(content)
                if content is not full:
                    print(dim(f"    (model gets start + end; full {len(full)} chars saved as "
                              f"result #{len(_saved)})"))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                         "content": content})
    print("stopped: too many tool rounds — ask again to continue.")
