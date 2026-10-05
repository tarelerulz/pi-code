"""Talking to the llama.cpp router: plain JSON calls and streaming chat."""
import json
import sys
import urllib.error
import urllib.request

from . import config
from .context import clean, fit_context
from .plugins import model_adapters_for
from .ui import dim


def api(path, body=None, timeout=None):
    req = urllib.request.Request(
        config.BASE_URL + path,
        json.dumps(body).encode() if body is not None else None,
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout or config.DEFAULT_TIMEOUT) as r:
        return json.load(r)


def list_models():
    for m in api("/v1/models")["data"]:
        mods = "+".join(m["architecture"]["input_modalities"])
        print(f"  {m['id']:<40} {m['status']['value']:<9} {mods}")


def chat_stream(body, model=None):
    """POST a streaming chat request; print the reply as it arrives.

    Returns (message, finish_reason, timings). The model's thinking
    (reasoning_content) is shown dimmed but not kept in the history."""
    req = urllib.request.Request(
        config.BASE_URL + "/v1/chat/completions",
        json.dumps(dict(body, stream=True)).encode(),
        {"Content-Type": "application/json"})
    content, calls, finish, timings = "", {}, None, None
    printed = 0          # chars of `content` already printed
    thinking = False
    tty = sys.stdout.isatty()
    quirks = model_adapters_for(body.get("model", model))

    def flush_content(final=False):
        nonlocal printed, content
        # Model adapters may hold back the start of a reply (hold) until
        # they can tidy it (clean), e.g. ASR models' "language X<asr_text>".
        if printed == 0:
            if not final and any(getattr(q, "hold", lambda t: False)(content) for q in quirks):
                return
            for q in quirks:
                if hasattr(q, "clean"):
                    try:
                        content = q.clean(content)
                    except Exception as exc:
                        print(f"pi-code: model adapter {q.__name__} failed: {exc}", file=sys.stderr)
        if len(content) > printed:
            if printed == 0:
                sys.stdout.write("\n")
            sys.stdout.write(content[printed:])
            sys.stdout.flush()
            printed = len(content)

    with urllib.request.urlopen(req, timeout=config.DEFAULT_TIMEOUT) as r:
        for raw in r:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if "error" in chunk:
                raise RuntimeError(chunk["error"].get("message", str(chunk["error"])))
            timings = chunk.get("timings") or timings
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    if not thinking:
                        sys.stdout.write("\033[2m" if tty else "")
                        thinking = True
                    sys.stdout.write(delta["reasoning_content"])
                    sys.stdout.flush()
                if delta.get("content"):
                    if thinking:
                        sys.stdout.write("\033[0m\n" if tty else "\n")
                        thinking = False
                    content += delta["content"]
                    flush_content()
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {
                        "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
                finish = choice.get("finish_reason") or finish
    if thinking:
        sys.stdout.write("\033[0m\n" if tty else "\n")
    flush_content(final=True)
    if printed:
        sys.stdout.write("\n")
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg, finish, timings


def call_model(msgs, model, tools):
    """One model call with the context budget; retries once, tighter, if the
    server still says the conversation is too long."""
    budget = config.CONTEXT_CHARS
    for attempt in range(2):
        fit_context(msgs, budget)
        body = {"model": model, "messages": clean(msgs), "max_tokens": config.MAX_TOKENS}
        if tools:
            body["tools"] = tools
        try:
            return chat_stream(body)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="replace")
            if attempt == 0 and "context" in err.lower():
                print(dim("  (too long for the model's memory — trimming harder and retrying)"))
                budget //= 2
                continue
            raise RuntimeError(f"{e} {err[:300]}") from e
