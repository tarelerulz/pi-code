# pi-code

A tiny, dependency-free agent CLI for a local [llama.cpp](https://github.com/ggml-org/llama.cpp) server running in **router mode**. It's a Claude-Code-style loop: you give it a task, it calls the server's own built-in tools (`read_file`, `write_file`, `edit_file`, `exec_shell_command`, etc.) and any [MCP](https://modelcontextprotocol.io/) tools the server has registered, actually writes and runs code, reads the real output, and iterates until it has an answer.

It's a single Python file. Stdlib only — no `pip install` required unless you want the optional web-search tool.

## Requirements

- Python 3.8+
- A `llama-server` build with router mode and built-in tools support (`--models-preset`, `--tools all`). MCP support (`--mcp-servers-config`) is optional but recommended if you want it to reach beyond the filesystem (web search, weather, whatever you wire up).
- At least one model preset reliable enough to drive a tool-calling loop — see [Tool-calling reliability](#tool-calling-reliability) below.

## Quick start

Start your router server with tools enabled:

```bash
llama-server \
  --models-preset your-presets.ini \
  --models-max 1 \
  --tools all \
  --host 127.0.0.1 --port 8091
```

Then just run it:

```bash
pi-code "write a C program that prints the first 20 Fibonacci numbers, compile it, and run it"
```

Watch the terminal — it prints each tool call as it happens (`write_file`, `exec_shell_command`, the real exit code) so you can see what actually ran, not just what the model claims it did.

## Usage

```
pi-code                          interactive session (default model: chat)
pi-code "make me a C app ..."    run this task, then keep talking
pi-code --once "task ..."        run one task and exit (for scripts)
pi-code -m coder-3b ...          pick another model/preset
pi-code -T -m vision -f photo.jpg "What is in this picture?"
pi-code -l                       list models
pi-code -y ...                   don't ask before running tools (yolo)
pi-code --list-tools             list available tool names
pi-code -t local_search_notes,web_search "what did I decide about X?"
```

| Flag | Meaning |
|---|---|
| `-m, --model NAME` | router preset to use (default: `$PI_CODE_DEFAULT_MODEL`, or auto-selected from `-f`) |
| `-y, --yes` | run tools without asking for confirmation each time |
| `-l, --list` | list available models/presets and exit |
| `-1, --once` | with a prompt: run it and exit instead of staying interactive |
| `-T, --no-tools` | start with tools off — plain chat, faster, no filesystem/shell access |
| `-f, --file PATH` | attach an image, audio, video, or text file (repeatable) |
| `--list-tools` | list available tool names (with a short description each) and exit |
| `-t, --tools-only NAME[,NAME...]` | only load these tools instead of the full set — fewer tools in the schema is more reliable on small/quantized models (they're more prone to picking the wrong tool, or hallucinating a call, when several compete for attention) and cheaper in context/prefill time. Errors out on an unrecognized name instead of silently ignoring it. |

In an interactive session:

| Command | Effect |
|---|---|
| `/model NAME` | switch model/preset (loads on next message) |
| `/models` | list models |
| `/tools` | toggle tools on/off |
| `/clear` | forget conversation history |
| `/quit` | exit |

## How it works

- On startup it fetches the live tool list from the server's `/tools` endpoint — anything the server has registered (built-in tools, any MCP server tools) is automatically available, no client-side tool definitions to maintain.
- File attachments (`-f`) are routed automatically by real MIME sniffing, not file extension: images → `$PI_CODE_VISION_MODEL`, audio → `$PI_CODE_AUDIO_MODEL`, video → `$PI_CODE_VIDEO_MODEL`, text → attached inline as content. Mixed modalities need an explicit `-m`.
- Each turn loops model ↔ tools (up to `$PI_CODE_MAX_ROUNDS` rounds) until the model returns a plain answer with no further tool calls.
- Destructive-ish tools (`exec_shell_command`, `write_file`, `edit_file`, `apply_diff`, `web_search`) ask for confirmation before running, unless `-y` is set or you've answered `a` (always) once already in the session.

## Configuration

All optional, all environment variables. Defaults shown:

| Variable | Default | Meaning |
|---|---|---|
| `PI_CODE_URL` | `http://localhost:8091` | router base URL |
| `PI_CODE_DEFAULT_MODEL` | `chat` | preset used when no `-m`/`-f` picks one |
| `PI_CODE_VISION_MODEL` | `lfm-vision` | preset for image attachments |
| `PI_CODE_AUDIO_MODEL` | `asr` | preset for audio attachments |
| `PI_CODE_VIDEO_MODEL` | `lfm-vision-1.6b` | preset for video attachments |
| `PI_CODE_MAX_ROUNDS` | `10` | max tool-call rounds per turn before giving up |
| `PI_CODE_TIMEOUT` | `900` | request timeout, in seconds — raise this on slow hardware, lower it to fail faster on a fast machine |
| `PI_CODE_START_HINT` | generic message | command/hint printed if the router can't be reached — set this to your actual startup script/command |

The four model-preset variables only matter if your router's presets aren't named `chat`/`lfm-vision`/`asr`/`lfm-vision-1.6b` — set them to whatever your own `--models-preset` file calls the equivalent presets.

## Web search

`pi-code` has no tools of its own — everything it offers the model, including web search, comes from the router's own `/tools` endpoint. A default web-search MCP server ships alongside this script:

- `pi-code-tools-mcp.py` — a small, portable stdio MCP server (needs the [`ddgs`](https://pypi.org/project/ddgs/) package: `pip install ddgs`)
- `pi-code-mcp.json` — an example `--mcp-servers-config` entry for it

Point your router at it directly (`--mcp-servers-config /path/to/pi-code-mcp.json`, editing the placeholder path inside first), or merge the `"web"` entry into your own MCP config if you already have one for other tools. Registered this way, it shows up to the model as `web_search` (the router prefixes MCP tool names as `{server_key}_{tool_name}` — `web` + `search` = `web_search`). Without any `--mcp-servers-config` at all, `pi-code` simply has no web access — nothing hardcoded, nothing to silently fail.

## Tool-calling reliability

Not every model reliably drives a tool-calling loop. Small or heavily quantized models can emit tool-call-*shaped text* in their reply instead of making an actual structured tool call — the difference matters: a real tool call gets executed and the result feeds back to the model; hallucinated tool-call text just sits there as a wrong answer.

There's no shortcut around this — test whichever preset you plan to use for tool-heavy tasks with something concrete (e.g. "write a file, compile it, run it") before trusting it unattended. For pure Q&A with a model you don't trust for tools, use `-T` to skip tool definitions entirely — smaller models tend to behave much better in plain chat mode than when tools are dangled in front of them.

## Known limitations

- No streaming output — you wait for each full turn.
- No persistent history across separate invocations; `/clear` only resets the current session.
- Single-file, single-conversation — no session management, no branching.
- Assumes an OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoint, plus llama.cpp's own `/tools` endpoint for tool discovery/execution — this is llama.cpp-router-specific, not a general OpenAI-API client.
