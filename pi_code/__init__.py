"""pi-code - tiny Claude-Code-style agent CLI for a llama.cpp router server.

Talks to an OpenAI-compatible llama.cpp server running in router mode
(--models-preset ..., ideally with --tools all and/or --mcp-servers-config).
The model writes files and runs shell commands through the server's built-in
tools, so compile errors and program output are real, not imagined.

pi-code has no hardcoded tools of its own -- everything it offers the model
comes from the router's own /tools endpoint (built-in file/shell tools plus
whatever's in --mcp-servers-config). A default web-search MCP server ships
alongside this script (pi-code-tools-mcp.py + pi-code-mcp.json) -- point your
router's --mcp-servers-config at it (or merge it into your own config) to
get a "web_search" tool; without it, pi-code has no web access at all.

Usage:
  pi-code                          interactive session (default model: see below)
  pi-code "make me a C app ..."    run this task, then keep talking
  pi-code --once "task ..."        run one task and exit (for scripts)
  pi-code -m coder-3b ...          pick another model/preset
  pi-code -T -m vision -f photo.jpg "What is in this picture?"
  pi-code -l                       list models
  pi-code -y ...                   don't ask before running tools (yolo)
  pi-code --picker all ...         send every tool's instructions up front
                                    (default: a name list plus a Laya hint;
                                    the model loads what it needs)
  pi-code --list-tools             list available tool names
  pi-code -t local_search_notes,web_search "what did I decide about X?"
                                    only load these tools (fewer tools in the
                                    schema is more reliable on small models,
                                    and cheaper in context/prefill time)

In the session:  /model NAME  /models  /clear  /quit

Configuration (environment variables, all optional — defaults shown):
  PI_CODE_URL=http://localhost:8091   router base URL
  PI_CODE_DEFAULT_MODEL=chat          preset used with no -m/-f
  PI_CODE_VISION_MODEL=lfm-vision     preset for image attachments
  PI_CODE_AUDIO_MODEL=asr             preset for audio attachments
  PI_CODE_VIDEO_MODEL=lfm-vision-1.6b preset for video attachments
  PI_CODE_MAX_ROUNDS=10               max tool-call rounds per turn
  PI_CODE_TIMEOUT=900                 request timeout, seconds
  PI_CODE_MAX_TOKENS=1024             longest reply per model call
  PI_CODE_CONTEXT_CHARS=12000         chat history kept per request (chars,
                                       not counting the tool list); oldest
                                       tool results shrink first, then the
                                       oldest turns are dropped
  PI_CODE_TOOL_CHARS=2500             longest tool result sent as-is; bigger
                                       ones are saved to ~/.cache/pi-code and
                                       the model gets the start, the end and
                                       a read_saved_result tool for the rest
  PI_CODE_TOOL_PICKER=laya            how tools reach the model:
                                       none = name list + load_tools/call_tool
                                       (the model asks for what it needs);
                                       any picker file in PI_CODE_PICKERS
                                       (e.g. laya) = same, plus a "likely
                                       tool" hint on each question; unknown
                                       or missing picker = none;
                                       all = every tool up front. -t NAMES
                                       always sends exactly those tools.
  PI_CODE_PICKERS=~/.config/pi-code/pickers    picker plug-ins: NAME.py
                                       defining pick(question, tools)
  PI_CODE_ADAPTERS=~/.config/pi-code/adapters  tool adapters: one .py per
                                       tool with optional TOOLS/PREFIX,
                                       CONFIRM, summary(), schema(),
                                       fix_args(), condense(). A tool no
                                       adapter marks CONFIRM = False asks
                                       "run? [Y/n]" first.
  PI_CODE_MODEL_ADAPTERS=~/.config/pi-code/models  model quirks: .py with
                                       optional MODELS, hold(text), clean(text)
  PI_CODE_FULL_TOOL_DOCS=             set to 1 to send the tools' full
                                       descriptions (default: first sentence
                                       only, plus any adapter schema() -- the
                                       Pi reads ~10 tokens/s, so every word
                                       costs time)
  PI_CODE_TOOL_SOURCES=router         where tools come from (also --sources):
                                       router, mcp (pi-code starts the MCP
                                       servers in PI_CODE_MCP_CONFIG, default
                                       ~/llama-mcp.json), local (pi-code's own
                                       file/shell tools); "mcp,local" = no
                                       router tools at all
  PI_CODE_SOURCES_DIR=~/.config/pi-code/sources   extra tool sources:
                                       NAME.py with list_tools() + call()
  PI_CODE_PROMPTS_DIR=~/.config/pi-code/prompts   system.txt (tools on) and
                                       plain.txt (tools off) replace the
                                       model's built-in instructions;
                                       PI_CODE_SYSTEM_PROMPT /
                                       PI_CODE_PLAIN_PROMPT override both
  PI_CODE_START_HINT=                 command/hint shown if the router
                                       can't be reached

Not every model reliably drives a tool-calling loop — small/quantized
models can hallucinate tool-call-shaped text instead of making a real
tool call. Test whichever preset you point at tool-heavy tasks before
trusting it; use -T for plain Q&A with anything that turns out unreliable.

Code layout (split from one file 2026-10-04; ~/.local/bin/pi-code is a
launcher):
  config.py    PI_CODE_* settings          ui.py        dim/bold text
  plugins.py   adapters, pickers, models   schemas.py   lean schemas, catalog,
  results.py   saved long tool results                  argument repair
  context.py   chat history budget         server.py    router calls, streaming
  deferred.py  load_tools / call_tool      files.py     -f attachments
  sources/     where tools come from: router.py, mcp.py (MCP client), local.py
  agent.py     the model <-> tools loop    cli.py       options + chat prompt
Tests: test_pi_code.py (tests/ in the repo)
"""

# The package's public names, for scripts and tests ("import pi_code as pc").
from . import config  # noqa: E402,F401
from .agent import (PLAIN_SYSTEM_PROMPT, SYSTEM_PROMPT, last_question,  # noqa: E402,F401
                    plain_prompt, run_turn, system_msg, system_prompt)
from .context import clean, fit_context, msg_chars  # noqa: E402,F401
from .deferred import (CALL_TOOL_TOOL, LOAD_TOOLS_TOOL, _loaded, _tool_mode,  # noqa: E402,F401
                       check_args, load_tools, tools_for_request)
from .files import choose_model, detect_mime, file_modality, user_content  # noqa: E402,F401
from .plugins import (ADAPTERS, MODEL_ADAPTERS, PICKERS, adapters_for,  # noqa: E402,F401
                      condense_result, fix_args, model_adapters_for, needs_confirm,
                      tool_summary)
from .results import READ_SAVED_TOOL, _saved, read_saved, save_result, shorten_result  # noqa: E402,F401
from .schemas import (catalog_line, coerce_args, first_sentence, lean_tool,  # noqa: E402,F401
                      resolve_tool_names, tool_catalog, tool_manual)
from .server import api, call_model, chat_stream, list_models  # noqa: E402,F401
