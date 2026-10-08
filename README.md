# pi-code

A tiny, dependency-free agent CLI for a local [llama.cpp](https://github.com/ggml-org/llama.cpp) server running in **router mode**. It's a Claude-Code-style loop: you give it a task, it calls the server's own built-in tools (`read_file`, `write_file`, `edit_file`, `exec_shell_command`, etc.) and any [MCP](https://modelcontextprotocol.io/) tools the server has registered, actually writes and runs code, reads the real output, and iterates until it has an answer.

Pure Python 3, standard library only — no `pip install` required unless you want the optional web-search tool.

It is built for **slow hardware** (developed on a Raspberry Pi 400 that reads prompts at ~10 tokens/s): tools are offered as a one-line list and their manuals loaded only when needed, long tool results are squeezed or saved for later, the tool list never changes mid-session so the server's prompt cache stays valid, and an optional small "picker" model can hint the right tool to a small main model. Everything that varies between setups — tools, where tools come from, the tool picker, model quirks, the model's instructions — is a plug-in file or a setting.

## Install

```bash
git clone https://github.com/tarelerulz/pi-code.git
ln -s "$PWD/pi-code/pi-code" ~/.local/bin/pi-code     # or put the folder on your PATH
mkdir -p ~/.config/pi-code && cp -r pi-code/examples/* ~/.config/pi-code/   # optional plug-ins
python3 pi-code/tests/test_pi_code.py                  # 31 offline tests, ~5 s
```

The examples are the plug-ins pi-code is developed with: tool adapters for the
llama.cpp router's file/shell tools, web search, a weather MCP server
([weather-mcp](https://www.npmjs.com/package/weather-mcp)) and a personal
notes/dictation server; the [Laya](https://huggingface.co/ggml-org/Laya-GGUF)
tool picker; and a fix for speech models' `<asr_text>` tag. Edit or delete
them to fit your own tools — pi-code works with none installed.

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
pi-code --tune -m quick          measure this machine, save settings for that model
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
| `--tune [--target SECONDS]` | measure how fast this machine runs the model (`-m`) and save settings that keep a step near the target (default 60 s); see [Tuning for a slow machine](#tuning-for-a-slow-machine) |
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

- On startup it gets the live tool list from its tool sources (by default the router's `/tools` endpoint: its built-in tools plus any MCP server tools; or its own MCP client and file/shell tools — see [Tool sources](#tool-sources-with-or-without-the-routers-tools)). No tool definitions to maintain.
- File attachments (`-f`) are routed automatically by real MIME sniffing, not file extension: images → `$PI_CODE_VISION_MODEL`, audio → `$PI_CODE_AUDIO_MODEL`, video → `$PI_CODE_VIDEO_MODEL`, text → attached inline as content. Mixed modalities need an explicit `-m`.
- Each turn loops model ↔ tools (up to `$PI_CODE_MAX_ROUNDS` rounds) until the model returns a plain answer with no further tool calls.
- Replies stream: text appears as it is generated, and a thinking model's reasoning is shown dimmed (but not kept in the history, to save context).
- Tools ask "run? [Y/n/a]" before running unless a tool adapter marks them safe (`CONFIRM = False`); the example adapters mark the read-only ones safe and keep shell, write, edit and web search asking. `-y`, or answering `a` once, skips the question for the session.

## Code layout

`pi-code` is a 12-line launcher; the code is the `pi_code` package next to it
(it finds the package through symlinks):

| file | job |
|---|---|
| `config.py` | all `PI_CODE_*` settings |
| `ui.py` | dim/bold terminal text |
| `plugins.py` | loads tool adapters, pickers, model adapters |
| `schemas.py` | lean tool schemas, the one-line catalog, manuals, string→number repair |
| `results.py` | saving long tool results, `read_saved_result` |
| `context.py` | fitting the chat history into the model's memory |
| `server.py` | router calls, streaming replies, retry on "too long" |
| `deferred.py` | `load_tools` / `call_tool`, argument checks |
| `sources/` | where tools come from: `router.py`, `mcp.py` (MCP client), `local.py` (+ `local_tools.json`) |
| `files.py` | `-f` attachments (image/audio/video/text) |
| `agent.py` | the model ↔ tools loop |
| `cli.py` | command-line options and the chat prompt |
| `tune.py` | `--tune`: measures this machine's reading/writing speed and sizes the settings per model |

Scripts can `import pi_code` (with the repo folder on `sys.path`); settings are
read as `pi_code.config.NAME` at call time.

## Tool sources: with or without the router's tools

`--sources` / `PI_CODE_TOOL_SOURCES` (comma-separated, default `router`):

- `router` — the llama.cpp router's `/tools` (its built-in file/shell tools
  plus its `--mcp-servers-config` servers). The old behaviour.
- `mcp` — pi-code starts the MCP servers itself from `PI_CODE_MCP_CONFIG`
  (default `~/llama-mcp.json`, the router's own config format), speaks MCP over
  stdio, and names tools `<server>_<tool>` exactly like the router. Servers are
  stopped when pi-code exits; their stderr goes to `~/.cache/pi-code/mcp-<server>.log`.
  `PI_CODE_MCP_TIMEOUT` (default 120 s) per request.
- `local` — pi-code's own `read_file`, `write_file`, `edit_file`,
  `exec_shell_command`, `file_glob_search`, `grep_search`, `get_info`, with the
  router's schemas and reply formats (no `.gitignore` filtering; common junk
  folders are skipped). Relative paths resolve in pi-code's own folder.

`--sources mcp,local` gives the same 11 tools as the router, without using the
router for tools at all (the router still runs the models). When two sources
offer a tool with the same name, the first listed wins.

More sources are plug-ins: any `NAME.py` in `~/.config/pi-code/sources/`
(`PI_CODE_SOURCES_DIR`) with `list_tools()` (tool definitions) and
`call(name, args)` (returns text) becomes `--sources NAME`; a file named like a
built-in source replaces it.

## The model's instructions

`~/.config/pi-code/prompts/system.txt` (tools on) and `plain.txt` (tools off),
if present, replace the built-in instructions (`PI_CODE_PROMPTS_DIR` moves the
folder); `PI_CODE_SYSTEM_PROMPT` / `PI_CODE_PLAIN_PROMPT` override both. A new
instruction text makes the router re-read it once (slow first question).

## Plug-ins: tool adapters, pickers, model adapters

pi-code itself has no tool- or model-specific code. Three optional folders
hold plug-ins; a file that fails to load is skipped with a warning, and
pi-code works with all three empty.

- `~/.config/pi-code/adapters/` (`PI_CODE_ADAPTERS`): one `.py` per tool or
  tool family. It may define `TOOLS = [...]` or `PREFIX = "..."` (which tools
  it handles), `CONFIRM` (`True`/`False` for all its tools, or a list of names
  that ask "run? [Y/n]" first), `summary(name, args)` (one-line display),
  `schema(tool)` (a shorter definition to send; skipped with
  `PI_CODE_FULL_TOOL_DOCS=1`), `fix_args(name, args)` and `condense(name, text)`.
  **A tool that no adapter marks `CONFIRM = False` asks before running** — with
  the folder missing, pi-code asks too often rather than running a command
  unasked. Shipped: `files-and-shell.py`, `web.py`, `local-tools.py`, `weather.py`.
- `~/.config/pi-code/pickers/` (`PI_CODE_PICKERS`): `NAME.py` defining
  `pick(question, tools) -> [tool names]`; chosen with `--picker NAME` or
  `PI_CODE_TOOL_PICKER`. Shipped: `laya.py` (the default).
- `~/.config/pi-code/models/` (`PI_CODE_MODEL_ADAPTERS`): model quirks. A file
  may define `MODELS` (preset names; missing = all), `hold(text)` (don't print
  the start of a reply yet) and `clean(text)` (tidy it). Shipped: `asr-tag.py`
  (strips `language English<asr_text>`).

Generic argument repair stays in pi-code: numbers/booleans sent as strings are
converted using the tool's schema, and calls with unknown or missing argument
names are not run (the tool's manual is returned instead).

## Tests

`python3 tests/test_pi_code.py` — 31 offline tests in
~5 s (plug-in loading, ask-first rules, schemas, argument repair, weather
condensing, saved results, context trimming, deferred tools, streaming with a
fake server, the local file/shell tools, the MCP client with a fake MCP server, instruction files, and two whole
conversations through the real agent loop with a scripted fake model server
and a fake tool-source plug-in). No router or model needed: the tool list and two weather reports
are saved in `tests/fixtures/`, and the tests use the plug-ins in `examples/`.
Run it after changing pi-code or a plug-in.

## Tuning for a slow machine

pi-code was built on a Raspberry Pi 400 (4 × Cortex-A72, 4 GB, CPU only), where a model reads a few hundred
words in the time a desktop GPU reads a book. This section is the whole method, with the Pi's real numbers.

### One rule

On a slow machine, one step (one model call) costs about

```
time ≈ tokens the model has to read ÷ reading speed  +  tokens it writes ÷ writing speed
```

Measured on the Pi 400 with LFM2.5-1.2B (Q4_0, llama.cpp, 3 threads): **reading ~10 tokens/s, writing ~4–5
tokens/s.** Reading 1,000 tokens therefore costs ~100 s before the first word appears. On a machine like
this, most of an agent's time goes into *reading* — instructions, tool lists, tool results, history — not
into answering. Every setting below works by making the model read less.

### Let pi-code measure it: `--tune`

```
pi-code --tune -m quick              # target: ~60 s per ordinary step
pi-code --tune -m quick --target 30
```

It sends four short requests and reports, for this machine and model:

- reading and writing speed (from the llama.cpp server's own timings)
- characters per token
- whether a follow-up step re-reads only the new part or the whole conversation (cache reuse)
- how big the instructions + tool list are, and the model's memory (`-c`) if the router reports it

Then it picks `PI_CODE_TOOL_CHARS`, `PI_CODE_CONTEXT_CHARS` and `PI_CODE_MAX_TOKENS` and saves them to
`~/.config/pi-code/tuned/<model>.json` (`PI_CODE_TUNED_DIR`). They are used automatically whenever that
model runs (pi-code says so at start and on `/model`); a `PI_CODE_*` variable you set yourself always wins.
Re-run it after changing hardware, model, quantization or server settings.

How it sizes things (constants at the top of `pi_code/tune.py`):

| Setting | Rule |
|---|---|
| `TOOL_CHARS` | a tool result is read right after it arrives: reading one full-size result takes half the target |
| `CONTEXT_CHARS` | the whole history is read again only when the server can't use its cache (first step, after trimming): such a step may take 4 × the target; never more than fits the model's memory with room for the reply |
| `MAX_TOKENS` | not cut for speed (a cut-off reply is a broken answer, not a faster one); only capped at a quarter of the model's memory. The time a full reply takes is reported instead |

What it printed on the Pi 400 (LFM2.5-1.2B, 2026-10-07):

```
  reading      10.7 tokens/s   (~4.27 characters per token)
  writing       4.8 tokens/s
  follow-up re-read 16 of 335 tokens
  every request carries 3037 characters of instructions + tool list
  model memory 4096 tokens

  setting          default  tuned
  CONTEXT_CHARS      12000   8000
  TOOL_CHARS          2500   1400
  MAX_TOKENS          1024   1024

  reading a full-size tool result: ~30.6 s; a step that re-reads everything: ~241.6 s
  - The instructions and tool list alone take ~66 s to read when nothing is cached. ...
  - Writing runs at 4.8 tokens/s: a short answer (~150 tokens) takes ~31 s ...
```

### What made the biggest difference on the Pi

All measured with the same question ("What's the weather at home?") and a 1.2B model:

| Change | Result |
|---|---|
| Starting point: every tool's full schema in every request, full weather report | ~7 min (2B model) / 153 s warm (1.2B) |
| Short tool descriptions (first sentence, fewer options): all tools 10,391 → 5,752 chars, plus condensed tool results (weather report 2,747 → 674 chars) | 153 s → 59 s (first step 52 s → 4 s) |
| Tools on demand (one-line catalog + `load_tools`/`call_tool`, the tool list never changes) | stays fast with many tools |
| Small server ubatch (`-ub 64`) for hybrid models | a new question re-read ~70 tokens instead of ~512 (~50 s saved per question) |
| Typical new question afterwards | ~35–55 s |

Lessons behind those numbers:

1. **Never change the start of the prompt mid-session.** llama.cpp reuses the part of the prompt it has
   already read. Chat templates put the tool list *first*, so adding one tool to the list made the server
   re-read everything (516 tokens, ~50 s). That is why pi-code's tool list is fixed and manuals arrive as
   tool results, and why today's date sits at the end of the system message.
2. **Hybrid models (LFM2.5, Qwen3.5) resume from checkpoints, not any token.** The server can only restart
   from a checkpoint set one ubatch before the end of the prompt, so with the default ubatch every new
   question re-read ~512 tokens. `-ub 64` (or `ub = 64` in a router preset) fixed it at no speed cost for
   text. `--tune` reports this case: "follow-up re-read N of M tokens" should be small.
3. **Shrink what tools return, not just what you send.** One long tool result costs more reading than the
   question and the answer together. Tool adapters (`condense()`) and `PI_CODE_TOOL_CHARS` handle it;
   the model can still fetch the rest with `read_saved_result`.
4. **Threads ≤ cores.** If you pin an engine to N cores (`taskset`), give it N threads or fewer. On the Pi a
   speech engine with 4 threads on 3 pinned cores was ~5× slower than with 3 (threads spin-waiting on each
   other); the same applies to llama.cpp's `-t`.
5. **Reasoning costs writing time.** A thinking model can spend the whole `MAX_TOKENS` budget thinking and
   never answer (seen with 1024 tokens). On a slow machine prefer a preset with reasoning off, or a model
   that doesn't think by default; pi-code warns when a reply is cut off.
6. **Some questions need no model.** Asking a 1.2B model for the weather took ~40–60 s; calling the weather
   tool directly takes ~0.5 s. A script beats an agent for fixed, frequent questions.

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
| `PI_CODE_MAX_FAILURES` | `3` | failed tool calls in a row that stop a turn (a repeated identical call is not re-run, and a 3rd one stops the turn); one short of the limit the model is told to stop and explain |
| `PI_CODE_TIMEOUT` | `900` | request timeout, in seconds — raise this on slow hardware, lower it to fail faster on a fast machine |
| `PI_CODE_MAX_TOKENS` | `1024` | longest reply per model call; pi-code warns when a reply hits it (thinking models can use it all up before answering) |
| `PI_CODE_CONTEXT_CHARS` | `12000` | chat history sent per request, in characters (tool list not counted). Over it, the oldest tool results are replaced by pointers first, then the oldest exchanges are dropped; tool calls and results stay paired. If the server still says it is too long, pi-code halves this once and retries |
| `PI_CODE_TOOL_CHARS` | `2500` | longest tool result sent to the model as-is. Bigger results are saved to `~/.cache/pi-code/results/<session>/<id>.txt`; the model gets the start, the end, and a local `read_saved_result` tool (by offset or `find` text) for the rest |
| `PI_CODE_TOOL_PICKER` | `laya` | how tools reach the model. `none`: the system message lists each tool as one line `name(args)`, and the model gets two fixed tools, `load_tools` (a tool's manual as a tool result) and `call_tool` (run a tool by name). The tool list never changes, so the router keeps the prompt cached. Calls with wrong argument names are not run; the manual is returned instead. `laya` (needs `examples/pickers/laya.py` installed, a llama.cpp `llama-server` new enough for `/v1/systemone`, and the Laya GGUF; otherwise it quietly acts as `none`): like `none`, plus the Laya decision model (own server on `PI_CODE_LAYA_PORT`, default 8096, started and stopped by pi-code; `PI_CODE_LAYA_SERVER`, `PI_CODE_LAYA_MODEL`) adds a one-line "likely tool" hint to each question, choosing from plain-word labels in `PI_CODE_LAYA_LABELS` (default `~/.config/pi-code/laya-labels.json`; unlabeled tools use their name). `all`: every tool's schema up front (old behaviour). `-t NAMES` always sends exactly those tools. Also `--picker` |
| `PI_CODE_TUNED_DIR` | `~/.config/pi-code/tuned` | per-model settings saved by `--tune` (`<model>.json`); they replace the defaults of `MAX_TOKENS`, `CONTEXT_CHARS` and `TOOL_CHARS` for that model, but a variable you set yourself wins |
| `PI_CODE_FULL_TOOL_DOCS` | unset | `1` sends full tool descriptions; default trims each to its first sentence and reduces the weather tool to place + days |
| `PI_CODE_START_HINT` | generic message | command/hint printed if the router can't be reached — set this to your actual startup script/command |

The four model-preset variables only matter if your router's presets aren't named `chat`/`lfm-vision`/`asr`/`lfm-vision-1.6b` — set them to whatever your own `--models-preset` file calls the equivalent presets.

## Web search

Web search comes from an MCP server. A default one ships alongside pi-code:

- `pi-code-tools-mcp.py` — a small, portable stdio MCP server (needs the [`ddgs`](https://pypi.org/project/ddgs/) package: `pip install ddgs`)
- `pi-code-mcp.json` — an example `--mcp-servers-config` entry for it

Point your router at it directly (`--mcp-servers-config /path/to/pi-code-mcp.json`, editing the placeholder path inside first), or merge the `"web"` entry into your own MCP config if you already have one for other tools. Registered this way, it shows up to the model as `web_search` (the router prefixes MCP tool names as `{server_key}_{tool_name}` — `web` + `search` = `web_search`). With `--sources mcp`, pi-code starts the servers in `PI_CODE_MCP_CONFIG` itself (same file format), so the router doesn't need it. Without any MCP config, `pi-code` simply has no web access — nothing hardcoded, nothing to silently fail.

## Tool-calling reliability

Not every model reliably drives a tool-calling loop. Small or heavily quantized models can emit tool-call-*shaped text* in their reply instead of making an actual structured tool call — the difference matters: a real tool call gets executed and the result feeds back to the model; hallucinated tool-call text just sits there as a wrong answer.

There's no shortcut around this — test whichever preset you plan to use for tool-heavy tasks with something concrete (e.g. "write a file, compile it, run it") before trusting it unattended. For pure Q&A with a model you don't trust for tools, use `-T` to skip tool definitions entirely — smaller models tend to behave much better in plain chat mode than when tools are dangled in front of them.

## Known limitations

- No persistent history across separate invocations; `/clear` only resets the current session.
- Single conversation — no session management, no branching.
- Chat uses the OpenAI-compatible `/v1/chat/completions` (streaming). `pi-code -l` and the `router` tool source rely on llama.cpp-router specifics (`/v1/models` fields, `/tools`); with `--sources mcp,local` tools need no router at all.
