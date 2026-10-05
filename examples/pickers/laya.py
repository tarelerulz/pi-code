"""pi-code tool picker: Laya (421M decision model, llama.cpp /v1/systemone).

Moved out of pi-code 2026-10-04. A picker is a .py file in
~/.config/pi-code/pickers/ (PI_CODE_PICKERS) defining
pick(question, tools) -> list of tool names; the file name is the picker name
(--picker laya / PI_CODE_TOOL_PICKER=laya). Return [] for "no guess".

Same setup as ~/voice-chat/talk.py: plain option words with "none" first.
Measured 2026-10-04 on 14 questions: 11/14 with the labels file below,
8/14 with tool names, quick alone 6/14; ~3.5 s per pick. Laya needs a
llama-server new enough for /v1/systemone, so it runs as its own small
server (~475 MB, 0% CPU idle); this reuses one already listening, otherwise
starts it and stops it when pi-code exits.
"""
import atexit
import json
import os
import subprocess
import sys
import time
import urllib.request

SERVER = os.path.expanduser(os.environ.get(
    "PI_CODE_LAYA_SERVER", "~/.local/bin/llama-server-systemone"))
MODEL = os.path.expanduser(os.environ.get(
    "PI_CODE_LAYA_MODEL", "~/models/laya/Laya-Q8_0.gguf"))
PORT = int(os.environ.get("PI_CODE_LAYA_PORT", "8096"))
# Optional {"tool_name": "plain words"} file: Laya picks far better from plain
# words ("weather", "run a shell command") than from tool names (11/14 vs 8/14).
LABELS = os.path.expanduser(os.environ.get(
    "PI_CODE_LAYA_LABELS", "~/.config/pi-code/laya-labels.json"))
LOG = os.path.expanduser("~/.cache/pi-code/laya-server.log")
_state = {"up": None, "proc": None}   # up: None = not tried yet


def _note(msg):
    print(f"\033[2m{msg}\033[0m" if sys.stdout.isatty() else msg)


def _healthy():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def _stop():
    proc = _state["proc"]
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _ensure():
    if _state["up"] is not None:
        return _state["up"]
    _state["up"] = False
    if _healthy():
        _state["up"] = True
        return True
    if not (os.path.isfile(SERVER) and os.path.isfile(MODEL)):
        _note("  (Laya picker not installed — continuing without it)")
        return False
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log = open(LOG, "w")
    _state["proc"] = subprocess.Popen(
        ["taskset", "-c", "1-3", SERVER, "-m", MODEL, "-t", "3",
         "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=log, stderr=log)
    atexit.register(_stop)
    deadline = time.time() + 60
    while time.time() < deadline and _state["proc"].poll() is None:
        if _healthy():
            _state["up"] = True
            return True
        time.sleep(0.5)
    _note(f"  (Laya picker did not start, see {LOG} — continuing without it)")
    _stop()
    return False


def pick(question, tools):
    if not question.strip() or not _ensure():
        return []
    try:
        with open(LABELS) as f:
            labels = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except (OSError, ValueError):
        labels = {}
    label_of = {t["function"]["name"]: labels.get(t["function"]["name"],
                t["function"]["name"].replace("_", " ")) for t in tools}
    name_of = {v: k for k, v in label_of.items()}
    criteria = {"none": None}
    criteria.update({label: None for label in label_of.values()})
    body = json.dumps({"state": question, "questions": {"tool": {
        "type": "choice", "instructions": "Which tool is needed to answer this request?",
        "criteria": criteria}}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/systemone", body,
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            choice = json.load(r)["answers"]["tool"]["choice"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [name_of[choice]] if choice in name_of else []
