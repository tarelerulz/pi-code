"""pi-code --tune: measure how fast this computer runs a model, then size the
settings so a step (one model call) stays near a time target.

On a slow machine one step costs about

    tokens the model reads / reading speed  +  tokens it writes / writing speed

(Pi 400 CPU, 2026-10: reading ~10 tokens/s, writing ~4-5 tokens/s with a 1.2B
model). The settings below only change how much there is to read, so --tune
measures this machine's speeds and works out how much text fits the target:

  TOOL_CHARS     a tool result is read right after it arrives, so it gets
                 TOOL_SHARE of the target
  CONTEXT_CHARS  the whole history is read again only when the server can't
                 reuse its cache (first step of a session, after old turns
                 are trimmed, ...), so that step may take COLD_FACTOR x target
  MAX_TOKENS     only capped by the model's memory (its -c size); the time a
                 full reply takes is reported, since cutting replies short
                 breaks answers rather than speeding them up

Results go to TUNED_DIR/<model>.json and apply automatically whenever that
model is used. A PI_CODE_* variable you set yourself always wins.
See "Tuning for a slow machine" in README-pi-code.md.
"""
import json
import os
import re
import time
import urllib.request

from . import config

TUNABLE = ("CONTEXT_CHARS", "TOOL_CHARS", "MAX_TOKENS")
DEFAULTS = {name: getattr(config, name) for name in TUNABLE}
DEFAULT_TARGET = 60          # seconds per ordinary step
TOOL_SHARE = 0.5             # share of the target for reading one tool result
COLD_FACTOR = 4              # a step that re-reads everything may take this x target
LIMITS = {"TOOL_CHARS": (800, 8000), "CONTEXT_CHARS": (3000, 48000), "MAX_TOKENS": (256, 1024)}

# Plain filler for the reading test. A random number goes first so the server
# can't answer it from cache.
FILLER = (
    "The river runs past the mill and the old stone bridge. Farmers bring "
    "their grain in the autumn, and the miller weighs each sack before it is "
    "ground. In winter the water is low and the wheel turns slowly. Children "
    "walk to school along the bank, and the baker opens his shop at six. "
) * 5


def tuned_path(model):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
    return os.path.join(config.TUNED_DIR, safe + ".json")


def load(model):
    try:
        with open(tuned_path(model)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def apply(model):
    """Use the saved tuning for this model (defaults if there is none).
    Settings given as PI_CODE_* variables are left alone. Returns the
    tuning record or None."""
    rec = load(model)
    chosen = (rec or {}).get("settings", {})
    for name in TUNABLE:
        if os.environ.get("PI_CODE_" + name):
            continue
        setattr(config, name, int(chosen.get(name, DEFAULTS[name])))
    return rec


def _post(model, messages, max_tokens):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": 0, "cache_prompt": True}
    req = urllib.request.Request(config.BASE_URL + "/v1/chat/completions",
                                 json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=config.DEFAULT_TIMEOUT) as r:
        reply = json.load(r)
    return reply, time.time() - t


def model_memory(model):
    """The model's context size (-c) from the router's model list, or None."""
    from .server import api
    try:
        for m in api("/v1/models")["data"]:
            if m["id"] != model:
                continue
            args = (m.get("status") or {}).get("args") or []
            for i, a in enumerate(args[:-1]):
                if a in ("-c", "--ctx-size"):
                    return int(args[i + 1])
    except Exception:
        pass
    return None


def fixed_chars(tools):
    """Size of what every request carries: instructions + tool list."""
    from .agent import system_msg
    from .deferred import tools_for_request
    return len(json.dumps(system_msg(tools))) + len(json.dumps(tools_for_request(tools)))


def measure(model, tools, say=print):
    """Time this machine running `model`. Four short requests."""
    say(f"  loading {model} (if it isn't already)...")
    _, load_s = _post(model, [{"role": "user", "content": "Say OK."}], 1)

    say("  reading speed...")
    text = f"Note {time.time_ns()}. " + FILLER
    msgs = [{"role": "user", "content": text + "\nReply with one word."}]
    reply, _ = _post(model, msgs, 1)
    t = reply.get("timings") or {}
    read_n, read_tps = t.get("prompt_n") or 0, t.get("prompt_per_second") or 0
    if not read_n or not read_tps:
        raise RuntimeError("the server sent no timings (pi-code --tune needs a llama.cpp server)")
    chars_per_token = len(text) / read_n

    say("  does the server reuse its cache for the next step?...")
    follow = msgs + [{"role": "assistant", "content": (reply["choices"][0]["message"].get("content") or "OK")},
                     {"role": "user", "content": "And one more word?"}]
    reply2, _ = _post(model, follow, 1)
    reread = (reply2.get("timings") or {}).get("prompt_n") or 0

    say("  writing speed...")
    reply3, _ = _post(model, [{"role": "user", "content":
                               "Write the numbers from one to eighty in words, separated by commas."}], 96)
    t3 = reply3.get("timings") or {}

    return {
        "load_s": round(load_s, 1),
        "read_tokens_per_s": round(read_tps, 1),
        "write_tokens_per_s": round(t3.get("predicted_per_second") or 0, 1),
        "chars_per_token": round(chars_per_token, 2),
        "test_prompt_tokens": read_n,
        "next_step_reread_tokens": reread,
        "fixed_chars": fixed_chars(tools),
        "model_memory_tokens": model_memory(model),
    }


def _clamp(name, value, step):
    lo, hi = LIMITS[name]
    return int(max(lo, min(hi, round(value / step) * step)))


def choose(m, target=DEFAULT_TARGET):
    """Settings for measurements `m` and a target in seconds, plus notes
    (plain sentences about anything the settings can't fix)."""
    read, write, cpt = m["read_tokens_per_s"], m["write_tokens_per_s"], m["chars_per_token"]
    fixed_tokens = m["fixed_chars"] / cpt
    memory = m.get("model_memory_tokens")
    notes = []

    max_tokens = DEFAULTS["MAX_TOKENS"]
    if memory:
        max_tokens = min(max_tokens, memory // 4)
    max_tokens = _clamp("MAX_TOKENS", max_tokens, 64)

    tool_chars = _clamp("TOOL_CHARS", TOOL_SHARE * target * read * cpt, 100)

    history_tokens = COLD_FACTOR * target * read - fixed_tokens
    if memory:   # never more than fits the model's memory with room for the reply
        history_tokens = min(history_tokens, 0.85 * (memory - max_tokens - fixed_tokens))
    context_chars = _clamp("CONTEXT_CHARS", history_tokens * cpt, 500)

    fixed_s = fixed_tokens / read
    if fixed_s > target:
        notes.append(f"The instructions and tool list alone take ~{fixed_s:.0f} s to read when nothing "
                     f"is cached. Keep the default picker (tools load on demand), or use -t NAMES to "
                     f"send only the tools a task needs.")
    if m["next_step_reread_tokens"] > 0.5 * m["test_prompt_tokens"]:
        notes.append(f"The server re-read {m['next_step_reread_tokens']} of {m['test_prompt_tokens']} tokens "
                     f"for a follow-up, so every step pays for the whole conversation. Hybrid models "
                     f"(LFM2.5, Qwen3.5) resume only from a checkpoint one ubatch before the end: "
                     f"a small ubatch (llama.cpp -ub 64) made new questions re-read ~70 tokens "
                     f"instead of ~512 on a Pi 400.")
    if write:
        notes.append(f"Writing runs at {write} tokens/s: a short answer (~150 tokens) takes "
                     f"~{150 / write:.0f} s and a full {max_tokens}-token reply ~{max_tokens / write / 60:.1f} min. "
                     f"Settings can't speed that up; a smaller model or reasoning off can.")
    if memory and context_chars <= LIMITS["CONTEXT_CHARS"][0] and history_tokens * cpt < LIMITS["CONTEXT_CHARS"][0]:
        notes.append("The model's memory is small: long sessions will forget older turns quickly.")

    settings = {"CONTEXT_CHARS": context_chars, "TOOL_CHARS": tool_chars, "MAX_TOKENS": max_tokens}
    timing = {
        "tool_result_read_s": round(tool_chars / cpt / read, 1),
        "full_reread_s": round((fixed_tokens + context_chars / cpt) / read, 1),
        "fixed_read_s": round(fixed_s, 1),
    }
    return settings, timing, notes


def run(model, tools, target=DEFAULT_TARGET, save=True, say=print):
    """Measure, choose, print, save. Returns the record."""
    say(f"Tuning pi-code for model {model} on this machine (target ~{target} s per step).")
    m = measure(model, tools, say)
    settings, timing, notes = choose(m, target)
    rec = {"model": model, "date": time.strftime("%Y-%m-%d %H:%M"), "target_s": target,
           "measured": m, "settings": settings, "expected": timing, "notes": notes}
    say("")
    say(f"  reading   {m['read_tokens_per_s']:>7} tokens/s   (~{m['chars_per_token']} characters per token)")
    say(f"  writing   {m['write_tokens_per_s']:>7} tokens/s")
    say(f"  follow-up re-read {m['next_step_reread_tokens']} of {m['test_prompt_tokens']} tokens")
    say(f"  every request carries {m['fixed_chars']} characters of instructions + tool list")
    if m.get("model_memory_tokens"):
        say(f"  model memory {m['model_memory_tokens']} tokens")
    say("")
    say("  setting          default  tuned")
    for name in TUNABLE:
        say(f"  {name:<15} {DEFAULTS[name]:>8} {settings[name]:>6}")
    say("")
    say(f"  reading a full-size tool result: ~{timing['tool_result_read_s']} s; "
        f"a step that re-reads everything: ~{timing['full_reread_s']} s")
    for n in notes:
        say("  - " + n)
    if save:
        os.makedirs(config.TUNED_DIR, exist_ok=True)
        with open(tuned_path(model), "w") as f:
            json.dump(rec, f, indent=1)
        say(f"\nSaved to {tuned_path(model)}; used automatically with -m {model}.")
        overridden = [n for n in TUNABLE if os.environ.get("PI_CODE_" + n)]
        if overridden:
            say("Your own " + ", ".join("PI_CODE_" + n for n in overridden) + " still win(s).")
    return rec
