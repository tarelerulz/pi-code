"""The agent loop: one user turn = model <-> tools until a plain answer."""
import datetime
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
    # Today's date goes last: questions like "next month" or "tomorrow" need
    # it, and it changes only once a day (one re-read of the prompt per day).
    today = f"\n\nToday is {datetime.date.today():%A, %B %d, %Y}."
    if not tools:
        return {"role": "system", "content": plain_prompt() + today}
    content = system_prompt()
    if _tool_mode[0] == "deferred":
        content += "\n\n" + tool_catalog(tools)
    return {"role": "system", "content": content + today}


def last_question(msgs):
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(
                p.get("text", "") for p in c if p.get("type") == "text")
    return ""


# pi-code's own helpers only read; they never ask "run?" (load_tools did,
# under the ask-unless-marked-safe default, 2026-10-04).
INTERNAL_TOOLS = {"load_tools", "read_saved_result"}

FAILURE_STARTS = ("tool error", "Not run (", "Unknown tool", "No tool by that name", '{"error"')


def is_failure(content):
    """A tool result that means the call did not work (any source's error style)."""
    return content.lstrip().startswith(FAILURE_STARTS)


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
            # Tools from the same server (same "<server>_" prefix) come along:
            # a picker that sees only names can't tell flights_search from
            # flights_cheapest_weekend, the main model can (2026-10-04).
            family = {g.split("_", 1)[0] + "_" for g in guess}
            hinted = [t for t in tools if t["function"]["name"] in guess
                      or t["function"]["name"].startswith(tuple(family))][:3]
            # one catalog line each, not the manual: every word costs ~0.1 s to read
            lines = [catalog_line(t) for t in hinted]
            msgs[-1]["content"] += "\n\n(Likely tool, run with call_tool: " + "; ".join(lines) + ")"
    # Repeated-failure guard (2026-10-04): a small model once went round for
    # 15 minutes retrying broken flight searches. Within one turn: the same
    # call twice is not run again; failures in a row get a nudge one short of
    # the limit, and at config.MAX_FAILURES (or a 3rd identical call) the turn
    # stops and says why.
    seen = {}
    failures = 0
    stop = None
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

            if stop:
                msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": "Not run: stopped after repeated failures."})
                continue
            sig = name + json.dumps(args, sort_keys=True, default=str)
            seen[sig] = seen.get(sig, 0) + 1
            if seen[sig] > 1:
                content = (f"Not run (repeated): you already called {name} with exactly these "
                           "arguments in this turn; its result is above. Use it, or try "
                           "something different.")
                print(dim("    " + content))
                failures += 1
                if seen[sig] >= 3 or failures >= config.MAX_FAILURES:
                    stop = f"the model repeated the same {name} call {seen[sig]} times"
                msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": content})
                continue

            skip = False
            if name not in INTERNAL_TOOLS and needs_confirm(name) and not always:
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
            if not skip:
                failures = failures + 1 if is_failure(content) else 0
                if failures >= config.MAX_FAILURES:
                    stop = f"{failures} tool calls failed in a row"
                elif failures == config.MAX_FAILURES - 1 and failures:
                    content += (f"\n\n(That is {failures} failed tool calls in a row. If you cannot "
                                "fix it, stop calling tools and tell the user what went wrong.)")
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
        if stop:
            print(bold(f"  [stopped: {stop}. Rephrase the question, or ask for a smaller step.]"))
            return
    print("stopped: too many tool rounds — ask again to continue.")
