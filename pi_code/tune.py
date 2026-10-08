"""pi-code --tune: measure how fast this computer runs a model, then size the
settings so a step (one model call) stays near a time target, and say what
else on this machine is slowing it down.

On a slow machine one step costs about

    tokens the model reads / reading speed  +  tokens it writes / writing speed

(Pi 400 CPU, 2026-10: reading ~10 tokens/s, writing ~4-5 tokens/s with a 1.2B
model). --tune measures, for this machine and model:

  reading speed     short prompts (twice) and one long prompt; long prompts
                    read slower per token on a CPU
  writing speed     and whether the model "thinks" before it answers
  cache reuse       does a follow-up re-read only the new part?
  fixed cost        the exact token count of pi-code's instructions + tool
                    list (server count: prompt_n + cache_n)
  the machine       (when the server runs here) cores, free memory, swapping
                    during the test, CPU temperature and clock (throttling)
  server settings   threads, ubatch, context size, model file (from the
                    llama.cpp router's model list)

and from that sets

  TOOL_CHARS     a tool result is read right after it arrives, so it gets
                 TOOL_SHARE of the target (short-prompt reading speed)
  CONTEXT_CHARS  the whole history is read again only when the server can't
                 reuse its cache (first step, after trimming, ...), so that
                 step may take COLD_FACTOR x target (long-prompt speed)
  MAX_TOKENS     only capped by the model's memory (its -c size): cutting a
                 reply short breaks the answer rather than speeding it up
  TIMEOUT        long enough for the slowest step this machine can have
                 (whole re-read + a full reply), so a reply is never thrown
                 away half-way; never below the default

Results go to TUNED_DIR/<model>.json and apply automatically whenever that
model is used. A PI_CODE_* variable you set yourself always wins.
`--tune -m a,b,c` tunes several models and compares their speed.
See "Tuning for a slow machine" in README-pi-code.md.
"""
import json
import os
import re
import statistics
import time
import urllib.parse
import urllib.request

from . import config

# PI_CODE_<key> -> config attribute
TUNABLE = {"CONTEXT_CHARS": "CONTEXT_CHARS", "TOOL_CHARS": "TOOL_CHARS",
           "MAX_TOKENS": "MAX_TOKENS", "TIMEOUT": "DEFAULT_TIMEOUT"}
DEFAULTS = {key: getattr(config, attr) for key, attr in TUNABLE.items()}
DEFAULT_TARGET = 60          # seconds per ordinary step
TOOL_SHARE = 0.5             # share of the target for reading one tool result
COLD_FACTOR = 4              # a step that re-reads everything may take this x target
TIMEOUT_MARGIN = 1.5         # slowest possible step x this
TYPICAL_STEP = (300, 150)    # tokens read, tokens written: used to compare models
# Swapping during the test, in MB (4 KB pages). A little is normal, e.g. a zram
# swap on the Pi 400 moved 0.2-3.5 MB during tests that ran at full speed.
SWAP_NOTE_MB, SWAP_WARN_MB = 10, 25
LIMITS = {"TOOL_CHARS": (800, 8000), "CONTEXT_CHARS": (3000, 48000),
          "MAX_TOKENS": (256, 1024), "TIMEOUT": (DEFAULTS["TIMEOUT"], 7200)}

# Plain filler for the reading tests. A fresh number goes first each time so
# the server can't answer from its cache.
FILLER = (
    "The river runs past the mill and the old stone bridge. Farmers bring "
    "their grain in the autumn, and the miller weighs each sack before it is "
    "ground. In winter the water is low and the wheel turns slowly. Children "
    "walk to school along the bank, and the baker opens his shop at six. "
)
SHORT_REPEAT, LONG_REPEAT = 5, 20          # ~330 and ~1300 tokens


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
    for key, attr in TUNABLE.items():
        if os.environ.get("PI_CODE_" + key):
            continue
        setattr(config, attr, int(chosen.get(key, DEFAULTS[key])))
    return rec


# ---------------------------------------------------------------- measuring

def _post(model, messages, max_tokens, tools=None):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": 0, "cache_prompt": True}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(config.BASE_URL + "/v1/chat/completions",
                                 json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=max(config.DEFAULT_TIMEOUT, 1800)) as r:
        reply = json.load(r)
    return reply, time.time() - t


def server_is_here():
    host = urllib.parse.urlparse(config.BASE_URL).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def machine_state():
    """What /proc and /sys say right now (Linux; missing parts are None)."""
    mem = dict(re.findall(r"^(\w+):\s+(\d+) kB", _read("/proc/meminfo"), re.M))
    vm = dict(re.findall(r"^(pswpin|pswpout) (\d+)", _read("/proc/vmstat"), re.M))
    temp = _read("/sys/class/thermal/thermal_zone0/temp").strip()
    cur = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").strip()
    top = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq").strip()
    return {
        "cores": os.cpu_count(),
        "mem_available_mb": int(mem["MemAvailable"]) // 1024 if "MemAvailable" in mem else None,
        "swap_total_mb": int(mem["SwapTotal"]) // 1024 if "SwapTotal" in mem else None,
        "swap_pages": int(vm["pswpin"]) + int(vm["pswpout"]) if len(vm) == 2 else None,
        "temp_c": round(int(temp) / 1000) if temp.isdigit() else None,
        "cpu_mhz": int(cur) // 1000 if cur.isdigit() else None,
        "cpu_max_mhz": int(top) // 1000 if top.isdigit() else None,
    }


def server_settings(model):
    """Launch settings the llama.cpp router reports for this model."""
    from .server import api
    want = {"--threads": "threads", "-t": "threads", "--threads-batch": "threads_batch",
            "-tb": "threads_batch", "--ubatch-size": "ubatch", "-ub": "ubatch",
            "--ctx-size": "memory_tokens", "-c": "memory_tokens", "--model": "model_file",
            "-m": "model_file"}
    found = {}
    try:
        for m in api("/v1/models")["data"]:
            if m["id"] != model:
                continue
            args = (m.get("status") or {}).get("args") or []
            for i, a in enumerate(args[:-1]):
                if a in want:
                    found[want[a]] = args[i + 1]
    except Exception:
        pass
    for k in ("threads", "threads_batch", "ubatch", "memory_tokens"):
        if k in found:
            try:
                found[k] = int(found[k])
            except ValueError:
                del found[k]
    if "model_file" in found:
        try:
            found["model_mb"] = os.path.getsize(found["model_file"]) // (1024 * 1024)
        except OSError:
            pass
    return found


def _tokens(reply):
    t = reply.get("timings") or {}
    return (t.get("prompt_n") or 0) + (t.get("cache_n") or 0)


def _read_test(model, repeat):
    text = f"Note {time.time_ns()}. " + FILLER * repeat
    msgs = [{"role": "user", "content": text + "\nReply with one word."}]
    reply, _ = _post(model, msgs, 1)
    t = reply.get("timings") or {}
    if not t.get("prompt_n") or not t.get("prompt_per_second"):
        raise RuntimeError("the server sent no timings (pi-code --tune needs a llama.cpp server)")
    return msgs, reply, t["prompt_per_second"], len(text) / t["prompt_n"], t["prompt_n"]


def measure(model, tools, say=print):
    """Time this machine running `model`: seven short requests."""
    from .agent import system_msg
    from .deferred import tools_for_request
    here = server_is_here()
    before = machine_state() if here else {}

    say(f"  loading {model} (if it isn't already)...")
    _, load_s = _post(model, [{"role": "user", "content": "Say OK."}], 1)

    say("  reading speed (2 short prompts, 1 long)...")
    msgs, first, s1, cpt, short_n = _read_test(model, SHORT_REPEAT)
    _, _, s2, _, _ = _read_test(model, SHORT_REPEAT)
    _, _, long_tps, _, long_n = _read_test(model, LONG_REPEAT)

    say("  does the server reuse its cache for the next step?...")
    follow = msgs + [{"role": "assistant", "content": first["choices"][0]["message"].get("content") or "OK"},
                     {"role": "user", "content": "And one more word?"}]
    reply2, _ = _post(model, follow, 1)
    reread = (reply2.get("timings") or {}).get("prompt_n") or 0

    say("  size of pi-code's instructions + tool list...")
    probe = f"Note {time.time_ns()}. Reply OK."
    reply3, _ = _post(model, [system_msg(tools), {"role": "user", "content": probe}], 1,
                      tools_for_request(tools))
    fixed_tokens = max(0, _tokens(reply3) - round(len(probe) / cpt) - 8)   # minus the probe + its wrapping

    say("  writing speed...")
    reply4, _ = _post(model, [{"role": "user", "content":
                               "Write the numbers from one to eighty in words, separated by commas."}], 96)
    t4 = reply4.get("timings") or {}
    msg4 = reply4["choices"][0]["message"]
    thought, said = len(msg4.get("reasoning_content") or ""), len(msg4.get("content") or "")

    after = machine_state() if here else {}
    m = {
        "load_s": round(load_s, 1),
        "read_tokens_per_s": round(statistics.median([s1, s2]), 1),
        "read_tokens_per_s_samples": [round(s1, 1), round(s2, 1)],
        "read_long_tokens_per_s": round(long_tps, 1),
        "long_prompt_tokens": long_n,
        "write_tokens_per_s": round(t4.get("predicted_per_second") or 0, 1),
        "thinks": thought > 0,
        "thinking_share": round(thought / (thought + said), 2) if thought + said else 0,
        "chars_per_token": round(cpt, 2),
        "test_prompt_tokens": short_n,
        "next_step_reread_tokens": reread,
        "fixed_tokens": fixed_tokens,
        "fixed_chars": len(json.dumps(system_msg(tools))) + len(json.dumps(tools_for_request(tools))),
        "server_here": here,
        "server": server_settings(model),
    }
    if here:
        m["machine"] = after
        if before.get("swap_pages") is not None and after.get("swap_pages") is not None:
            m["machine"]["swapped_pages_during_test"] = after["swap_pages"] - before["swap_pages"]
        m["machine"]["temp_c_before"] = before.get("temp_c")
    m["model_memory_tokens"] = m["server"].get("memory_tokens")
    return m


# ---------------------------------------------------------------- choosing

def _clamp(key, value, step):
    lo, hi = LIMITS[key]
    return int(max(lo, min(hi, round(value / step) * step)))


def typical_step_s(m):
    r, w = TYPICAL_STEP
    return round(r / m["read_tokens_per_s"] + w / max(m["write_tokens_per_s"], 0.1), 1)


def _advice(m, settings, timing, target):
    notes = []
    srv, mach = m.get("server") or {}, m.get("machine") or {}
    cores = mach.get("cores")

    # pi-code itself
    if timing["fixed_read_s"] > target:
        notes.append(f"The instructions and tool list ({m['fixed_tokens']} tokens) take ~{timing['fixed_read_s']:.0f} s "
                     f"to read when nothing is cached. Keep the default picker (tools load on demand), or use "
                     f"-t NAMES to send only the tools a task needs.")
    if m["read_long_tokens_per_s"] < 0.8 * m["read_tokens_per_s"]:
        notes.append(f"Long prompts read slower ({m['read_long_tokens_per_s']} vs {m['read_tokens_per_s']} tokens/s "
                     f"at {m['long_prompt_tokens']} tokens): history was sized with the slower speed.")

    # server settings
    if m["next_step_reread_tokens"] > 0.5 * m["test_prompt_tokens"]:
        ub = srv.get("ubatch")
        tail = (f" Your ubatch is {ub}; try -ub 64 (router preset: ub = 64)." if ub and ub > 64
                else " Try a small ubatch: -ub 64 (router preset: ub = 64).")
        notes.append(f"The server re-read {m['next_step_reread_tokens']} of {m['test_prompt_tokens']} tokens for a "
                     f"follow-up, so every step pays for the whole conversation. Hybrid models (LFM2.5, Qwen3.5) "
                     f"resume only from a checkpoint one ubatch before the end; on a Pi 400 -ub 64 made new "
                     f"questions re-read ~70 tokens instead of ~512." + tail)
    if cores:
        for key, label in (("threads", "-t (threads for writing)"), ("threads_batch", "-tb (threads for reading)")):
            n = srv.get(key)
            if n and n > cores:
                notes.append(f"The server uses {label} = {n} on a {cores}-core machine. More threads than cores "
                             f"makes them wait on each other (~5x slower measured on a Pi); use {cores} or fewer.")
        if srv.get("threads") and srv["threads"] == cores and cores <= 4:
            notes.append(f"Writing uses all {cores} cores, leaving none for the rest of the system; "
                         f"-t {cores - 1} is often as fast and keeps the machine responsive.")

    # memory
    avail, swap = mach.get("mem_available_mb"), mach.get("swapped_pages_during_test")
    swap_mb = (swap or 0) * 4 / 1024
    if swap_mb >= SWAP_WARN_MB:
        notes.append(f"The system SWAPPED {swap_mb:.0f} MB during the test. The model and the server's cache don't "
                     f"fit in memory comfortably: expect sudden 10x slowdowns. Use a smaller model or quantization, "
                     f"a smaller -c, --cache-ram, or close other programs.")
    elif swap_mb >= SWAP_NOTE_MB:
        notes.append(f"Some swapping during the test ({swap_mb:.0f} MB): memory is getting tight.")
    if avail is not None and avail < 400:
        notes.append(f"Only {avail} MB of memory left with the model loaded. Another program starting could push "
                     f"the machine into swap.")
    if srv.get("model_mb") and avail is not None and srv["model_mb"] > 2 * avail:
        notes.append(f"The model file is {srv['model_mb']} MB with {avail} MB free: a bigger model won't fit here.")

    # the model
    if m["thinks"]:
        notes.append(f"This model thinks before answering ({int(m['thinking_share'] * 100)}% of the writing test was "
                     f"thinking). On a slow machine that is the slowest part: use a preset with reasoning off "
                     f"(router: reasoning = off), or a model that doesn't think. Thinking can also use up all "
                     f"{settings['MAX_TOKENS']} reply tokens before any answer.")
    if m["write_tokens_per_s"]:
        notes.append(f"Writing runs at {m['write_tokens_per_s']} tokens/s: a short answer (~150 tokens) takes "
                     f"~{150 / m['write_tokens_per_s']:.0f} s and a full {settings['MAX_TOKENS']}-token reply "
                     f"~{settings['MAX_TOKENS'] / m['write_tokens_per_s'] / 60:.1f} min. Settings can't speed that up; "
                     f"a smaller model or reasoning off can.")

    # the measurement itself
    temp, mhz, top = mach.get("temp_c"), mach.get("cpu_mhz"), mach.get("cpu_max_mhz")
    if (temp and temp >= 80) or (mhz and top and mhz < 0.9 * top):
        notes.append(f"The CPU was hot or slowed down during the test ({temp} °C, {mhz} of {top} MHz): speeds "
                     f"may be low. Re-run --tune when the machine is cool, or improve its cooling.")
    s1, s2 = m["read_tokens_per_s_samples"]
    if min(s1, s2) < 0.85 * max(s1, s2):
        notes.append(f"The two reading tests differed ({s1} vs {s2} tokens/s): something else was using the CPU. "
                     f"Re-run --tune on an idle machine.")
    return notes


def choose(m, target=DEFAULT_TARGET):
    """Settings for measurements `m` and a target in seconds, the expected
    timings, and notes (plain sentences about what else slows this machine)."""
    read, cpt = m["read_tokens_per_s"], m["chars_per_token"]
    read_long = min(read, m.get("read_long_tokens_per_s") or read)
    write = max(m["write_tokens_per_s"], 0.1)
    fixed_tokens = m.get("fixed_tokens") or m["fixed_chars"] / cpt
    memory = m.get("model_memory_tokens")

    max_tokens = DEFAULTS["MAX_TOKENS"]
    if memory:
        max_tokens = min(max_tokens, memory // 4)
    max_tokens = _clamp("MAX_TOKENS", max_tokens, 64)

    tool_chars = _clamp("TOOL_CHARS", TOOL_SHARE * target * read * cpt, 100)

    history_tokens = COLD_FACTOR * target * read_long - fixed_tokens
    if memory:   # never more than fits the model's memory with room for the reply
        history_tokens = min(history_tokens, 0.85 * (memory - max_tokens - fixed_tokens))
    context_chars = _clamp("CONTEXT_CHARS", history_tokens * cpt, 500)

    full_reread_s = (fixed_tokens + context_chars / cpt) / read_long
    slowest_s = full_reread_s + max_tokens / write + m.get("load_s", 0)
    timeout = _clamp("TIMEOUT", TIMEOUT_MARGIN * slowest_s, 60)

    settings = {"CONTEXT_CHARS": context_chars, "TOOL_CHARS": tool_chars,
                "MAX_TOKENS": max_tokens, "TIMEOUT": timeout}
    timing = {
        "tool_result_read_s": round(tool_chars / cpt / read, 1),
        "full_reread_s": round(full_reread_s, 1),
        "fixed_read_s": round(fixed_tokens / read_long, 1),
        "slowest_step_s": round(slowest_s),
        "typical_step_s": typical_step_s(m),
    }
    notes = _advice(m, settings, timing, target)
    if memory and history_tokens * cpt < LIMITS["CONTEXT_CHARS"][0]:
        notes.append("The model's memory is small: long sessions will forget older turns quickly.")
    return settings, timing, notes


# ---------------------------------------------------------------- running

def _report(model, m, settings, timing, notes, say):
    srv, mach = m.get("server") or {}, m.get("machine") or {}
    say("")
    say(f"  reading   {m['read_tokens_per_s']:>7} tokens/s   (short prompts: {m['read_tokens_per_s_samples'][0]}, "
        f"{m['read_tokens_per_s_samples'][1]}; {m['long_prompt_tokens']} tokens: {m['read_long_tokens_per_s']})")
    say(f"  writing   {m['write_tokens_per_s']:>7} tokens/s   (thinks first: {'yes' if m['thinks'] else 'no'})")
    say(f"  follow-up re-read {m['next_step_reread_tokens']} of {m['test_prompt_tokens']} tokens; "
        f"~{m['chars_per_token']} characters per token")
    say(f"  every request carries {m['fixed_tokens']} tokens of instructions + tool list")
    if srv:
        say("  server: " + ", ".join(f"{k} {v}" for k, v in srv.items() if k != "model_file"))
    if mach:
        say(f"  machine: {mach.get('cores')} cores, {mach.get('mem_available_mb')} MB free, "
            f"swapped during test: {(mach.get('swapped_pages_during_test') or 0) * 4 / 1024:.1f} MB, "
            f"{mach.get('temp_c')} °C, {mach.get('cpu_mhz')}/{mach.get('cpu_max_mhz')} MHz")
    elif not m["server_here"]:
        say("  machine: the server runs elsewhere; memory/heat checks skipped")
    say("")
    say("  setting          default  tuned")
    for key in TUNABLE:
        say(f"  {key:<15} {DEFAULTS[key]:>8} {settings[key]:>6}")
    say("")
    say(f"  reading a full-size tool result: ~{timing['tool_result_read_s']} s; a step that re-reads "
        f"everything: ~{timing['full_reread_s']} s; slowest possible step: ~{timing['slowest_step_s']} s")
    for n in notes:
        say("  - " + n)


def run_one(model, tools, target=DEFAULT_TARGET, save=True, say=print):
    """Measure, choose, print, save one model. Returns the record."""
    say(f"Tuning pi-code for model {model} on this machine (target ~{target} s per step).")
    m = measure(model, tools, say)
    settings, timing, notes = choose(m, target)
    rec = {"model": model, "date": time.strftime("%Y-%m-%d %H:%M"), "target_s": target,
           "measured": m, "settings": settings, "expected": timing, "notes": notes}
    _report(model, m, settings, timing, notes, say)
    if save:
        os.makedirs(config.TUNED_DIR, exist_ok=True)
        with open(tuned_path(model), "w") as f:
            json.dump(rec, f, indent=1)
        say(f"\nSaved to {tuned_path(model)}; used automatically with -m {model}.")
        overridden = [k for k in TUNABLE if os.environ.get("PI_CODE_" + k)]
        if overridden:
            say("Your own " + ", ".join("PI_CODE_" + k for k in overridden) + " still win(s).")
    return rec


def compare(recs, say=print):
    """Speed table for several tuned models, fastest typical step first."""
    say("\nCompared on this machine (speed only; check each model's answers yourself):")
    say(f"  {'model':<16} {'typical step':>12} {'reading':>8} {'writing':>8} {'thinks':>6} {'file MB':>8}  notes")
    for r in sorted(recs, key=lambda r: r["expected"]["typical_step_s"]):
        m = r["measured"]
        warn = []
        if (m.get("machine") or {}).get("swapped_pages_during_test", 0) * 4 / 1024 >= SWAP_WARN_MB:
            warn.append("swapped")
        if m["next_step_reread_tokens"] > 0.5 * m["test_prompt_tokens"]:
            warn.append("no cache reuse")
        say(f"  {r['model']:<16} {r['expected']['typical_step_s']:>10} s {m['read_tokens_per_s']:>8} "
            f"{m['write_tokens_per_s']:>8} {'yes' if m['thinks'] else 'no':>6} "
            f"{(m.get('server') or {}).get('model_mb', '?'):>8}  {', '.join(warn)}")
    r, w = TYPICAL_STEP
    say(f"  (typical step = reading {r} new tokens + writing {w})")


def run(models, tools, target=DEFAULT_TARGET, save=True, say=print):
    """Tune each model (comma list); with several, print a comparison."""
    recs = []
    for i, model in enumerate(models):
        if i:
            say("")
        recs.append(run_one(model, tools, target, save, say))
    if len(recs) > 1:
        compare(recs, say)
    return recs
