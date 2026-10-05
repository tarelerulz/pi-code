"""Plug-in files: tool adapters, pickers and model adapters.

Tool-specific fixes live outside pi-code, one .py file per tool in
config.ADAPTERS_DIR; pickers (who guesses the tool before the model starts)
in config.PICKERS_DIR; model quirks in config.MODEL_ADAPTERS_DIR. See
~/.config/pi-code/adapters/weather.py, pickers/laya.py and models/asr-tag.py
for the names each kind of file may define. A file that fails to load is
skipped with a warning; pi-code works with all three folders empty.
"""
import glob
import importlib.util
import json
import os
import sys

from . import config


def _load_plugins(folder):
    plugins = []
    for path in sorted(glob.glob(os.path.join(folder, "*.py"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            spec = importlib.util.spec_from_file_location(f"pi_code_plugin_{name}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as exc:          # a broken plug-in must not break pi-code
            print(f"pi-code: skipping {path}: {exc}", file=sys.stderr)
            continue
        plugins.append((name, mod))
    return plugins


ADAPTERS = [mod for _, mod in _load_plugins(config.ADAPTERS_DIR)]
MODEL_ADAPTERS = [mod for _, mod in _load_plugins(config.MODEL_ADAPTERS_DIR)]
PICKERS = {"none": lambda question, tools: []}
PICKERS.update({name: mod.pick for name, mod in _load_plugins(config.PICKERS_DIR)
                if callable(getattr(mod, "pick", None))})


def adapters_for(name):
    return [a for a in ADAPTERS if name in getattr(a, "TOOLS", ())
            or (getattr(a, "PREFIX", None) and name.startswith(a.PREFIX))]


def _adapt(hook, name, value, *args):
    for a in adapters_for(name):
        fn = getattr(a, hook, None)
        if fn:
            try:
                value = fn(*args, value) if args else fn(value)
            except Exception as exc:
                print(f"pi-code: adapter {a.__name__} {hook} failed: {exc}", file=sys.stderr)
    return value


def needs_confirm(name):
    """Ask before running unless an adapter marks the tool safe.

    Adapter CONFIRM = True/False for all its tools, or a list of the names
    that need asking. No adapter says anything -> ask (safe default: with the
    adapters folder missing, pi-code asks too often rather than running a
    shell command unasked)."""
    for a in adapters_for(name):
        c = getattr(a, "CONFIRM", None)
        if isinstance(c, bool):
            return c
        if isinstance(c, (list, tuple, set)):
            return name in c
    return True


def tool_summary(name, args):
    """One-line display of a tool call: an adapter's summary(), else the args."""
    for a in adapters_for(name):
        fn = getattr(a, "summary", None)
        if fn:
            try:
                text = fn(name, args)
            except Exception:
                continue
            if text is not None:
                return text
    return json.dumps(args)[:120]


def model_adapters_for(model):
    return [a for a in MODEL_ADAPTERS
            if getattr(a, "MODELS", None) in (None, "*") or model in getattr(a, "MODELS", ())]


def fix_args(name, args):
    """Let adapters repair the model's arguments for this tool."""
    return _adapt("fix_args", name, dict(args), name)


def condense_result(name, text):
    """Let adapters shrink this tool's result before the model reads it."""
    return _adapt("condense", name, text, name)


def adapt_schema(tool):
    """Let adapters shorten this tool's definition."""
    return _adapt("schema", tool["function"]["name"], tool)
