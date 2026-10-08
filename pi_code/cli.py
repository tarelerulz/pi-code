"""Command line and interactive session."""
import argparse
import readline  # noqa: F401  (enables line editing on input())
import sys
import urllib.error

from . import __doc__ as USAGE
from . import config, sources
from .agent import run_turn, system_msg
from .deferred import _loaded, _tool_mode
from .files import choose_model, user_content
from .plugins import PICKERS
from .server import api, list_models
from .ui import bold, dim
from . import tune


def main():
    ap = argparse.ArgumentParser(prog="pi-code", add_help=True,
                                 description=USAGE.split("\n")[0])
    ap.add_argument("prompt", nargs="*", help="one-shot task (omit for interactive)")
    ap.add_argument("-m", "--model",
                    help="router preset (default: auto from -f, otherwise chat)")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="run tools without asking")
    ap.add_argument("-l", "--list", action="store_true", help="list models")
    ap.add_argument("--list-tools", action="store_true",
                    help="list available tool names, then exit")
    ap.add_argument("-t", "--tools-only", metavar="NAME[,NAME...]",
                    help="only load these tools (comma-separated names from "
                         "--list-tools) -- fewer tools is more reliable on "
                         "small/quantized models and cheaper in context")
    ap.add_argument("--picker", choices=["all"] + sorted(PICKERS), default=None,
                    help="how tools reach the model (default $PI_CODE_TOOL_PICKER or laya)")
    ap.add_argument("--sources", default=None, metavar="SRC[,SRC...]",
                    help=f"where tools come from: {', '.join(sources.KNOWN)} "
                         f"(default $PI_CODE_TOOL_SOURCES or {sources.DEFAULT})")
    ap.add_argument("-1", "--once", action="store_true",
                    help="with a prompt: run it and exit instead of staying interactive")
    ap.add_argument("-T", "--no-tools", action="store_true",
                    help="start with tools off (plain chat, faster)")
    ap.add_argument("--tune", action="store_true",
                    help="measure this machine with the model (-m; a,b,c compares several) "
                         "and save settings that keep a step near --target seconds")
    ap.add_argument("--target", type=float, default=tune.DEFAULT_TARGET, metavar="SECONDS",
                    help=f"with --tune: seconds per ordinary step (default {tune.DEFAULT_TARGET})")
    ap.add_argument("-f", "--file", action="append", default=[], metavar="PATH",
                    help="attach an image, audio, video, or text file (repeatable)")
    a = ap.parse_args()

    try:
        used = sources.use(a.sources or sources.DEFAULT)
    except ValueError as exc:
        print(exc)
        sys.exit(2)
    try:
        if a.list:
            list_models()
            return
        tools = sources.list_tools()
    except urllib.error.URLError:
        print(f"Cannot reach the router at {config.BASE_URL}.")
        print(f"Start it with: {config.START_HINT}")
        sys.exit(1)

    if a.list_tools:
        for t in tools:
            desc = t["function"]["description"].split("\n")[0]
            if len(desc) > 70:
                desc = desc[:69] + "…"
            print(f"{t['function']['name']:30s} {desc}")
        return

    if a.tools_only:
        wanted = {name.strip() for name in a.tools_only.split(",") if name.strip()}
        available = {t["function"]["name"] for t in tools}
        unknown = wanted - available
        if unknown:
            print(f"unknown tool name(s): {', '.join(sorted(unknown))}")
            print("see: pi-code --list-tools")
            sys.exit(2)
        tools = [t for t in tools if t["function"]["name"] in wanted]

    try:
        model = a.model or choose_model(a.file)
    except ValueError as exc:
        print(f"file routing error: {exc}")
        sys.exit(2)
    config.TOOL_PICKER = a.picker or config.TOOL_PICKER
    # -t is the hand-picked list: send exactly those, no catalog
    _tool_mode[0] = "all" if (a.tools_only or config.TOOL_PICKER == "all") else "deferred"
    if a.tune:
        try:
            tune.run([m.strip() for m in model.split(",") if m.strip()], tools, a.target)
        except (urllib.error.URLError, RuntimeError, KeyError) as exc:
            print(f"tuning failed: {exc}")
            sys.exit(1)
        return
    msgs = [system_msg([] if a.no_tools else tools)]

    # "pi-code fast" means the fast model, not the message "fast"
    if a.model is None and not a.file and len(a.prompt) == 1:
        try:
            known = {m["id"] for m in api("/v1/models")["data"]}
        except urllib.error.URLError:
            known = set()
        if a.prompt[0] in known:
            model = a.prompt[0]
            a.prompt = []

    print(f"pi-code — model: {model}  server: {config.BASE_URL}  "
          f"tools: {'+'.join(used)}  (/help for commands)")
    tuned = tune.apply(model)
    if tuned:
        print(dim(f"  tuned for this machine on {tuned['date']} (pi-code --tune -m {model} to redo)"))

    active_tools = [] if a.no_tools else tools

    if a.prompt or a.file:
        try:
            content = user_content(" ".join(a.prompt), a.file)
        except ValueError as exc:
            print(f"file error: {exc}")
            sys.exit(2)
        msgs.append({"role": "user", "content": content})
        try:
            run_turn(msgs, model, active_tools, a.yes)
        except KeyboardInterrupt:
            print("\n(interrupted — history kept)")
        # exit after the first task only if asked to, or when not on a
        # terminal (piped/scripted use) — otherwise keep the chat open
        if a.once or not sys.stdin.isatty():
            return

    while True:
        try:
            line = input(f"\n{bold(model)}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            return
        if line == "/help":
            print("  /model NAME   switch model      /models  list models")
            print("  /tools        toggle tools on/off (off = faster plain chat)")
            print("  /clear        forget the chat   /quit    exit")
            continue
        if line == "/tools":
            if active_tools:
                active_tools = []
                msgs[0] = system_msg([])
                print("tools OFF — plain chat (faster)")
            else:
                active_tools = tools
                msgs[0] = system_msg(tools)
                print("tools ON — model can write files and run commands")
            continue
        if line == "/models":
            list_models()
            continue
        if line.startswith("/model"):
            parts = line.split()
            if len(parts) == 2:
                model = parts[1]
                print(f"model set to {model} (loads on next message)"
                      + (" — using its --tune settings" if tune.apply(model) else ""))
            else:
                print(f"current model: {model}")
            continue
        if line == "/clear":
            msgs = [system_msg(active_tools)]
            _loaded.clear()
            print("history cleared")
            continue
        msgs.append({"role": "user", "content": line})
        try:
            run_turn(msgs, model, active_tools, a.yes)
        except KeyboardInterrupt:
            print("\n(interrupted — history kept)")
