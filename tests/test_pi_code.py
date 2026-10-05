#!/usr/bin/env python3
"""Offline tests for pi-code (the pi_code package) and its plug-ins (~/.config/pi-code).

Run:  python3 test_pi_code.py      (~5 s; tests/ in the repo)

Needs no router and no model: the router's tool list and two real weather
reports (NOAA: Denver, Open-Meteo: London) are saved in fixtures/ (captured
2026-10-04). Re-capture them if the
router's tools change. Plug-in folders and the weather locations file are the
real ones unless a test points pi-code somewhere else.
"""
import contextlib
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(HERE)              # ~/.local/bin: pi-code launcher + pi_code/
FIX = os.path.join(HERE, "fixtures")

# In the published repo the example plug-ins sit in examples/; use them so the
# tests don't depend on what is installed in ~/.config/pi-code.
EXAMPLES = os.path.join(BIN, "examples")
if os.path.isdir(EXAMPLES):
    for var, sub in (("PI_CODE_ADAPTERS", "adapters"), ("PI_CODE_PICKERS", "pickers"),
                     ("PI_CODE_MODEL_ADAPTERS", "models")):
        os.environ.setdefault(var, os.path.join(EXAMPLES, sub))

TMP = tempfile.mkdtemp(prefix="pi-code-test-")
LOCATIONS = os.path.join(TMP, "locations.json")
with open(LOCATIONS, "w") as f:
    json.dump({"home": {"alternateNames": ["my town", "my home town"]}}, f)
os.environ["PI_CODE_WEATHER_LOCATIONS"] = LOCATIONS      # read by adapters/weather.py

sys.path.insert(0, BIN)
import pi_code as pc  # noqa: E402

pc.config.RESULTS_DIR = os.path.join(TMP, "results")
with open(os.path.join(FIX, "tools.json")) as _f:
    TOOLS = json.load(_f)
WEATHER = "weather_get_weather_summary"


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def run_isolated(code, **env):
    """Run python code that loads pi-code with extra environment variables."""
    full = dict(os.environ, **env)
    pre = f"import json, sys; sys.path.insert(0, {BIN!r}); import pi_code as pc\n"
    r = subprocess.run([sys.executable, "-c", pre + code], env=full,
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip(), r.stderr.strip()


class Plugins(unittest.TestCase):
    def test_broken_plugin_is_skipped(self):
        d = tempfile.mkdtemp(dir=TMP)
        for name, text in (("good.py", "def pick(q, tools):\n    return []\n"),
                           ("broken.py", "this is not python (\n")):
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
        out, err = run_isolated("print(sorted(pc.PICKERS))", PI_CODE_PICKERS=d)
        self.assertEqual(out, "['good', 'none']")
        self.assertIn("skipping", err)

    def test_empty_folders_still_work(self):
        empty = tempfile.mkdtemp(dir=TMP)
        out, _ = run_isolated(
            "print(sorted(pc.PICKERS), len(pc.ADAPTERS), len(pc.MODEL_ADAPTERS),"
            " pc.needs_confirm('read_file'), pc.tool_summary('x', {'a': 1}))",
            PI_CODE_PICKERS=empty, PI_CODE_ADAPTERS=empty, PI_CODE_MODEL_ADAPTERS=empty)
        # no adapters -> every tool asks first (safe default)
        self.assertEqual(out, "['none'] 0 0 True {\"a\": 1}")

    def test_real_pickers_present(self):
        self.assertIn("laya", pc.PICKERS)
        self.assertIn("none", pc.PICKERS)


class Confirm(unittest.TestCase):
    def test_same_tools_ask_as_before(self):
        asks = {t["function"]["name"] for t in TOOLS if pc.needs_confirm(t["function"]["name"])}
        self.assertEqual(asks, {"exec_shell_command", "write_file", "edit_file", "web_search"})

    def test_unknown_tool_asks(self):
        self.assertTrue(pc.needs_confirm("brand_new_tool"))

    def test_summaries(self):
        self.assertEqual(pc.tool_summary("exec_shell_command", {"command": "ls"}), "ls")
        self.assertEqual(pc.tool_summary("read_file", {"path": "/x"}), "/x")
        self.assertEqual(pc.tool_summary("web_search", {"query": "q"}), "q")
        self.assertEqual(pc.tool_summary("grep_search", {"pattern": "x"}), '{"pattern": "x"}')


class Schemas(unittest.TestCase):
    def test_lean_trims_and_drops_noise(self):
        w = next(t for t in TOOLS if t["function"]["name"] == WEATHER)
        lean = pc.lean_tool(w)
        self.assertEqual(sorted(lean["function"]["parameters"]["properties"]),
                         ["city_name", "days", "location_name"])
        self.assertNotIn("minimum", json.dumps(lean))
        self.assertLess(len(json.dumps(lean)), 800)
        self.assertGreater(len(json.dumps(w)), 3000)     # original untouched

    def test_catalog_and_manual(self):
        self.assertGreater(len(json.dumps(pc.tool_manual([WEATHER], TOOLS))), 300)
        self.assertIn(f"{WEATHER}(location_name?, city_name?, days?)", pc.tool_catalog(TOOLS))
        self.assertIn("read_file(path, start_line?", pc.tool_catalog(TOOLS))

    def test_resolve_names(self):
        self.assertEqual(pc.resolve_tool_names(["weather"], TOOLS), [WEATHER])
        self.assertEqual(pc.resolve_tool_names("read_file", TOOLS), ["read_file"])
        self.assertEqual(pc.resolve_tool_names(["nothing_like_it"], TOOLS), [])


class Arguments(unittest.TestCase):
    def test_check_args(self):
        self.assertIsNone(pc.check_args(WEATHER, {"location_name": "home"}, TOOLS))
        bad = pc.check_args(WEATHER, {"location": "home"}, TOOLS)
        self.assertIn("unknown argument(s): location", bad)
        self.assertIn("location_name", bad)
        self.assertIn("missing: path", pc.check_args("read_file", {}, TOOLS))

    def test_coerce(self):
        self.assertEqual(pc.coerce_args(WEATHER, {"days": "1"}, TOOLS), {"days": 1})
        self.assertEqual(pc.coerce_args("read_file", {"start_line": "5", "append_loc": "true"}, TOOLS),
                         {"start_line": 5, "append_loc": True})
        self.assertEqual(pc.coerce_args("read_file", {"path": "123", "start_line": "abc"}, TOOLS),
                         {"path": "123", "start_line": "abc"})

    def test_weather_place_fixes(self):
        cases = [({"location_name": "Spokane"}, {"city_name": "Spokane"}),
                 ({"location_name": "Spokane", "city_name": "Spokane", "days": 1},
                  {"city_name": "Spokane", "days": 1}),
                 ({"location_name": "home"}, {"location_name": "home"}),
                 ({"city_name": "home"}, {"location_name": "home"}),
                 ({"city_name": "my home town"}, {"location_name": "home"}),
                 ({"city_name": "Boise"}, {"city_name": "Boise"})]
        for given, want in cases:
            self.assertEqual(pc.fix_args(WEATHER, given), want, given)
        self.assertEqual(pc.fix_args("read_file", {"city_name": "home"}), {"city_name": "home"})


class Results(unittest.TestCase):
    def test_condense_noaa(self):
        raw = fixture("weather-noaa.md")
        short = pc.condense_result(WEATHER, raw)
        self.assertLess(len(short), 900)
        self.assertTrue(short.startswith("Place: Denver"))
        self.assertIn("Now: temperature", short)
        self.assertIn("Alerts:", short)
        if "observation is" in raw:
            self.assertIn("WARNING: the 'Now' reading is", short)

    def test_stale_reading_warning_any_unit(self):
        raw = fixture("weather-noaa.md")
        for age in ("34.1 hours", "2 days"):
            stale = raw.replace("# Current Weather Conditions\n",
                                f"# Current Weather Conditions\n\nThis observation is {age} old\n", 1)
            self.assertIn(f"WARNING: the 'Now' reading is {age} old",
                          pc.condense_result(WEATHER, stale))

    def test_condense_open_meteo(self):
        short = pc.condense_result(WEATHER, fixture("weather-london.md"))
        self.assertIn("Place: Greater London", short)
        self.assertIn("High", short)
        self.assertIn("Alerts: none", short)

    def test_condense_leaves_others_alone(self):
        self.assertEqual(pc.condense_result("read_file", "# Weather Summary\nx"), "# Weather Summary\nx")
        self.assertEqual(pc.condense_result(WEATHER, "Error: nope"), "Error: nope")

    def test_shorten_and_read_back(self):
        big = "\n".join(f"line {i} " + "x" * 40 for i in range(500))
        short = pc.shorten_result(big)
        self.assertLess(len(short), pc.config.TOOL_CHARS + 300)
        rid = len(pc._saved)
        self.assertIn(f"read_saved_result with id {rid}", short)
        self.assertIn("line 250 ", pc.read_saved({"id": rid, "find": "line 250 "}))
        self.assertIn(f"chars 10000-", pc.read_saved({"id": rid, "offset": 10000}))
        self.assertIn("no saved result", pc.read_saved({"id": 999}))
        self.assertEqual(pc.shorten_result("small"), "small")


class Context(unittest.TestCase):
    def make(self, turns):
        msgs = [{"role": "system", "content": "s"}]
        for t in range(turns):
            msgs += [{"role": "user", "content": f"q{t}"},
                     {"role": "assistant", "content": "", "tool_calls": [
                         {"id": f"c{t}", "type": "function",
                          "function": {"name": "read_file", "arguments": "{}"}}]},
                     {"role": "tool", "tool_call_id": f"c{t}", "content": "y" * 2400},
                     {"role": "assistant", "content": f"a{t}"}]
        return msgs

    def test_trim_keeps_pairs_and_last_question(self):
        msgs = self.make(6)
        with contextlib.redirect_stdout(io.StringIO()):
            pc.fit_context(msgs, 6000)
        self.assertLessEqual(sum(pc.msg_chars(m) for m in msgs[1:]), 6000)
        calls = {tc["id"] for m in msgs for tc in m.get("tool_calls") or []}
        results = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
        self.assertEqual(calls, results)
        self.assertTrue(all("_trimmed" not in m for m in pc.clean(msgs)))
        with contextlib.redirect_stdout(io.StringIO()):
            pc.fit_context(msgs, 300)
        self.assertEqual([m["content"] for m in msgs if m["role"] == "user"], ["q5"])
        self.assertEqual(msgs[0]["role"], "system")


class Deferred(unittest.TestCase):
    def test_tool_list_stays_fixed(self):
        pc._tool_mode[0] = "deferred"
        before = [t["function"]["name"] for t in pc.tools_for_request(TOOLS)]
        self.assertIn(WEATHER, pc.load_tools({"names": ["weather"]}, TOOLS))
        after = [t["function"]["name"] for t in pc.tools_for_request(TOOLS)]
        self.assertEqual(before[:2], ["load_tools", "call_tool"])
        self.assertEqual(before[:2], after[:2])
        self.assertIn("No tool by that name", pc.load_tools({"names": ["zzz"]}, TOOLS))
        self.assertIn("call_tool", pc.system_msg(TOOLS)["content"])
        self.assertEqual(pc.system_msg([])["content"], pc.PLAIN_SYSTEM_PROMPT)

    def test_all_mode_sends_every_tool(self):
        pc._tool_mode[0] = "all"
        try:
            self.assertEqual(len([t for t in pc.tools_for_request(TOOLS)
                                  if t["function"]["name"] != "read_saved_result"]), len(TOOLS))
        finally:
            pc._tool_mode[0] = "deferred"


class Streaming(unittest.TestCase):
    CHUNKS = [{"choices": [{"delta": {"reasoning_content": "hmm "}}]},
              {"choices": [{"delta": {"content": "language English<asr"}}]},
              {"choices": [{"delta": {"content": "_text>Hello world"}}]},
              {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "function": {
                  "name": "read_", "arguments": "{\"pa"}}]}}]},
              {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
                  "name": "file", "arguments": "th\": \"x\"}"}}]}}]},
              {"choices": [{"delta": {}, "finish_reason": "length"}],
               "timings": {"predicted_per_second": 3.3}}]

    def test_stream_assembles_reply(self):
        chunks = self.CHUNKS

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for c in chunks:
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = pc.config.BASE_URL
        pc.config.BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                msg, finish, timings = pc.chat_stream({"model": "asr", "messages": []})
        finally:
            pc.config.BASE_URL = old
            srv.shutdown()
            srv.server_close()
        self.assertEqual(msg["content"], "Hello world")          # asr-tag model adapter
        self.assertNotIn("asr_text", out.getvalue())
        self.assertIn("hmm", out.getvalue())                      # thinking shown
        self.assertNotIn("reasoning_content", msg)                # ...but not kept
        self.assertEqual(msg["tool_calls"][0]["function"],
                         {"name": "read_file", "arguments": "{\"path\": \"x\"}"})
        self.assertEqual(finish, "length")
        self.assertEqual(timings["predicted_per_second"], 3.3)


class LocalSource(unittest.TestCase):
    """pi-code's own file/shell tools (sources/local.py), router-compatible replies."""

    def setUp(self):
        from pi_code.sources import local
        self.local = local
        self.d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(self.d, "sub"))
        os.makedirs(os.path.join(self.d, "node_modules"))
        for rel, text in (("a.txt", "alpha\nbeta moon\ngamma\n"), ("sub/b.py", "moonshine\n"),
                          ("node_modules/junk.py", "moon\n")):
            with open(os.path.join(self.d, rel), "w") as f:
                f.write(text)

    def call(self, name, **args):
        return self.local.call(name, args)

    def test_same_names_and_schemas_as_router(self):
        router = {t["function"]["name"]: t for t in TOOLS}
        for t in self.local.list_tools():
            self.assertEqual(t, router[t["function"]["name"]])

    def test_read(self):
        a = os.path.join(self.d, "a.txt")
        self.assertEqual(self.call("read_file", path=a), "alpha\nbeta moon\ngamma\n")
        self.assertEqual(self.call("read_file", path=a, start_line=2, append_loc=True),
                         "2→beta moon\n3→gamma\n")
        self.assertIn("cannot stat file", self.call("read_file", path=a + "x"))

    def test_glob_skips_junk(self):
        self.assertEqual(self.call("file_glob_search", path=self.d),
                         "a.txt\nsub/b.py\n\n---\nTotal matches: 2\n")
        self.assertEqual(self.call("file_glob_search", path=self.d, include="*.py", type="all"),
                         "sub/b.py\n\n---\nTotal matches: 1\n")

    def test_grep(self):
        self.assertEqual(self.call("grep_search", path=self.d, pattern="moon", return_line_numbers=True),
                         "a.txt:2:beta moon\nsub/b.py:1:moonshine\n\n---\nTotal matches: 2\n")
        a = os.path.join(self.d, "a.txt")
        out = self.call("grep_search", path=a, pattern="MOON", ignore_case=True, context_lines=1)
        self.assertEqual(out, f"{a}-1-alpha\n{a}:2:beta moon\n{a}-3-gamma\n--\n\n---\nTotal matches: 1\n")

    def test_shell(self):
        self.assertEqual(self.call("exec_shell_command", command="echo hi; echo err >&2; exit 3"),
                         "hi\nerr\n\n[exit code: 3]")
        self.assertIn("[timed out after 1 s]", self.call("exec_shell_command", command="sleep 5", timeout=1))

    def test_write_and_edit(self):
        p = os.path.join(self.d, "new", "c.txt")
        self.assertIn("file written successfully", self.call("write_file", path=p, content="one\ntwo\n"))
        self.assertIn('"edits_applied": 1',
                      self.call("edit_file", path=p, edits=[{"old_text": "two", "new_text": "TWO"}]))
        self.assertIn("could not find edits[0].old_text",
                      self.call("edit_file", path=p, edits=[{"old_text": "zzz", "new_text": "x"}]))
        with open(p) as f:
            self.assertEqual(f.read(), "one\nTWO\n")
        self.assertIn("error", self.call("read_file"))            # missing argument -> error text


FAKE_MCP = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    m, rid = msg["method"], msg["id"]
    if m == "initialize":
        res = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
               "serverInfo": {"name": "fake", "version": "1"}}
    elif m == "tools/list":
        res = {"tools": [{"name": "echo", "description": "Echo text back.",
                          "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                                          "required": ["text"]}}]}
    elif m == "tools/call":
        t = msg["params"]["arguments"].get("text", "")
        res = {"content": [{"type": "text", "text": "echo: " + t}], "isError": t == "fail"}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}), flush=True)
        continue
    print("not json noise", flush=True)
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}), flush=True)
'''


class McpSource(unittest.TestCase):
    """pi-code's own MCP client (sources/mcp.py) against a tiny fake server."""

    def test_list_and_call(self):
        d = tempfile.mkdtemp(dir=TMP)
        server = os.path.join(d, "fake_mcp.py")
        with open(server, "w") as f:
            f.write(FAKE_MCP)
        cfg = os.path.join(d, "mcp.json")
        with open(cfg, "w") as f:
            json.dump({"mcpServers": {"fake": {"command": sys.executable, "args": [server]}}}, f)
        out, err = run_isolated(
            "from pi_code import sources\n"
            "sources.use('mcp')\n"
            "tools = sources.list_tools()\n"
            "print(json.dumps([t['function']['name'] for t in tools]))\n"
            "print(sources.call('fake_echo', {'text': 'hi'}))\n"
            "print(sources.call('fake_echo', {'text': 'fail'}))\n"
            "print(sources.call('nope', {}))\n",
            PI_CODE_MCP_CONFIG=cfg, PI_CODE_MCP_TIMEOUT="10")
        lines = out.splitlines()
        self.assertEqual(lines, ['["fake_echo"]', "echo: hi", "tool error: echo: fail",
                                 "tool error: no source offers 'nope'"], err)

    def test_unknown_source_rejected(self):
        from pi_code import sources
        with self.assertRaises(ValueError):
            sources.use("router,bogus")


class Prompts(unittest.TestCase):
    def test_file_then_setting_override(self):
        d = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(d, "system.txt"), "w") as f:
            f.write("Custom tool rules.\n")
        out, _ = run_isolated("print(pc.system_prompt()); print(pc.plain_prompt() == pc.PLAIN_SYSTEM_PROMPT)",
                              PI_CODE_PROMPTS_DIR=d)
        self.assertEqual(out, "Custom tool rules.\nTrue")
        out, _ = run_isolated("print(pc.system_prompt())", PI_CODE_PROMPTS_DIR=d,
                              PI_CODE_SYSTEM_PROMPT="From the setting.")
        self.assertEqual(out, "From the setting.")
        out, _ = run_isolated("print(pc.system_msg([])['content'] == pc.PLAIN_SYSTEM_PROMPT)",
                              PI_CODE_PROMPTS_DIR=os.path.join(d, "missing"))
        self.assertEqual(out, "True")


# A tool source plug-in for the end-to-end tests: offers the real weather and
# shell tool definitions, answers weather with the saved NOAA report, and logs
# every call it gets.
FAKE_SOURCE = r'''
import json, os
FIX, LOG = os.environ["E2E_FIX"], os.environ["E2E_LOG"]
def list_tools():
    with open(os.path.join(FIX, "tools.json")) as f:
        return [t for t in json.load(f) if t["function"]["name"] in
                ("weather_get_weather_summary", "exec_shell_command")]
def call(name, args):
    with open(LOG, "a") as f:
        f.write(json.dumps([name, args]) + "\n")
    with open(os.path.join(FIX, "weather-noaa.md")) as f:
        return f.read() if name.startswith("weather") else "ran"
'''


class EndToEnd(unittest.TestCase):
    """The real agent loop (run_turn) against a scripted fake model server and
    a fake tool source plug-in: wrong argument -> manual -> retry -> answer."""

    def converse(self, question, script, stdin=""):
        requests = []

        class Model(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                step = script[min(len(requests), len(script)) - 1]
                chunks = ([{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"c{len(requests)}",
                            "function": {"name": "call_tool", "arguments": json.dumps(step)}}]}}]}]
                          if isinstance(step, dict) else
                          [{"choices": [{"delta": {"content": step}}]}])
                chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for c in chunks:
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d, "sources"))
        with open(os.path.join(d, "sources", "fake.py"), "w") as f:
            f.write(FAKE_SOURCE)
        log = os.path.join(d, "calls.log")
        srv = http.server.HTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        code = ("from pi_code import sources\n"
                "sources.use('fake')\n"
                "tools = sources.list_tools()\n"
                f"msgs = [pc.system_msg(tools), {{'role': 'user', 'content': {question!r}}}]\n"
                "pc.run_turn(msgs, 'm', tools, False)\n"
                "print('MSGS ' + json.dumps(msgs))\n")
        full = dict(os.environ, PI_CODE_SOURCES_DIR=os.path.join(d, "sources"),
                    PI_CODE_URL=f"http://127.0.0.1:{srv.server_address[1]}",
                    PI_CODE_TOOL_PICKER="none", E2E_FIX=FIX, E2E_LOG=log)
        pre = f"import json, sys; sys.path.insert(0, {BIN!r}); import pi_code as pc\n"
        try:
            r = subprocess.run([sys.executable, "-c", pre + code], env=full, input=stdin,
                               capture_output=True, text=True, timeout=60)
        finally:
            srv.shutdown()
            srv.server_close()
        out = r.stdout
        self.assertIn("MSGS ", out, r.stderr)
        msgs = json.loads(out.split("MSGS ", 1)[1])
        calls = []
        if os.path.exists(log):
            with open(log) as f:
                calls = [json.loads(line) for line in f]
        return out, msgs, requests, calls

    def test_weather_conversation(self):
        out, msgs, requests, calls = self.converse("What's the weather at home?", [
            {"name": WEATHER, "arguments": {"location": "home"}},                 # wrong name
            {"name": "weather", "arguments": {"location_name": "home", "days": "2"}},  # "2" as text
            "It is sunny at home."])
        self.assertEqual(len(requests), 3)
        # the tool list sent never changes (keeps the router's prompt cache valid)
        self.assertTrue(all([t["function"]["name"] for t in r["tools"]] == ["load_tools", "call_tool"]
                            for r in requests))
        self.assertIn("weather_get_weather_summary(location_name?, city_name?, days?)",
                      requests[0]["messages"][0]["content"])
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        self.assertTrue(tool_msgs[0]["content"].startswith("Not run (unknown argument(s): location)"))
        self.assertEqual(calls, [[WEATHER, {"location_name": "home", "days": 2}]])   # fixed + coerced
        self.assertTrue(tool_msgs[1]["content"].startswith("Place: Denver"))        # condensed by adapter
        self.assertLess(len(tool_msgs[1]["content"]), 900)
        self.assertEqual(msgs[-1], {"role": "assistant", "content": "It is sunny at home."})
        self.assertIn("It is sunny at home.", out)
        ids = [tc["id"] for m in msgs for tc in m.get("tool_calls") or []]
        self.assertEqual(ids, [m["tool_call_id"] for m in tool_msgs])

    def test_shell_asks_and_can_be_declined(self):
        out, msgs, requests, calls = self.converse("run uname -a", [
            {"name": "exec_shell_command", "arguments": {"command": "uname -a"}},
            "OK, not run."], stdin="n\n")
        self.assertIn("run? [Y/n/a(lways)]", out)
        self.assertEqual(calls, [])                                      # never ran
        self.assertEqual([m["content"] for m in msgs if m["role"] == "tool"],
                         ["User declined to run this tool."])


if __name__ == "__main__":
    unittest.main(verbosity=1)
