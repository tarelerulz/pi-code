"""Tool source: pi-code's own file and shell tools.

Same names, schemas (local_tools.json, copied from the llama.cpp router's
built-ins 2026-10-04) and reply formats as the router's tools, so adapters,
ask-first rules, picker labels and the model see no difference. Differences:
no .gitignore filtering (common junk folders are still skipped).
"""
import fnmatch
import json
import os
import re
import subprocess

with open(os.path.join(os.path.dirname(__file__), "local_tools.json")) as _f:
    TOOLS = json.load(_f)

READ_MAX = 16384          # whole-file read limit, like the router
LIST_MAX = 100
JUNK = {".git", "node_modules", "build", "dist", "__pycache__", ".venv", "venv",
        ".cache", ".mypy_cache", ".pytest_cache", "target"}


def list_tools():
    return json.loads(json.dumps(TOOLS))


def _err(msg):
    return json.dumps({"error": msg})


def _path(p):
    return os.path.abspath(os.path.expanduser(p or "."))


def _matcher(pattern):
    """Router rule: no '/' -> match the basename at any depth; with '/' ->
    match the relative path, prefixed with **/ unless anchored."""
    if not pattern or pattern == "**":
        return lambda rel: True
    if "/" not in pattern:
        return lambda rel: fnmatch.fnmatch(os.path.basename(rel), pattern)
    pat = pattern[1:] if pattern.startswith("/") else (
        pattern if pattern.startswith("**/") else "**/" + pattern)
    # fnmatch's * also matches '/', so "**/" can match nothing at the start
    return lambda rel: fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(
        rel, pat[3:] if pat.startswith("**/") else pat)


def _walk(base, max_depth=0):
    """(relative path, is_dir) under base, skipping junk folders."""
    base_depth = base.rstrip("/").count("/")
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in JUNK)
        depth = root.rstrip("/").count("/") - base_depth + 1
        for d in dirs:
            if not max_depth or depth <= max_depth:
                yield os.path.relpath(os.path.join(root, d), base), True
        for f in sorted(files):
            if not max_depth or depth <= max_depth:
                yield os.path.relpath(os.path.join(root, f), base), False
        if max_depth and depth >= max_depth:
            dirs[:] = []


def read_file(path, start_line=None, end_line=None, append_loc=False):
    p = _path(path)
    try:
        size = os.path.getsize(p)
    except OSError:
        return _err(f"cannot stat file: {p}")
    if start_line is None and end_line is None and size > READ_MAX:
        return _err(f"file too large ({size} bytes, max {READ_MAX}). "
                    "Use start_line/end_line to read a portion.")
    with open(p, errors="replace") as f:
        lines = f.read().splitlines(keepends=True)
    first = max(1, int(start_line or 1))
    last = int(end_line) if end_line else len(lines)
    chosen = lines[first - 1:last]
    if append_loc:
        chosen = [f"{n}→{line}" for n, line in enumerate(chosen, first)]
    return "".join(chosen)


def file_glob_search(path, include=None, exclude=None, type="file", max_depth=0, limit=LIST_MAX):
    base = _path(path)
    if not os.path.isdir(base):
        return _err(f"not a directory: {base}")
    inc, exc = _matcher(include), (_matcher(exclude) if exclude else lambda rel: False)
    want = type or "file"
    limit = min(int(limit or LIST_MAX), LIST_MAX)
    hits = []
    for rel, is_dir in _walk(base, int(max_depth or 0)):
        if (is_dir and want == "file") or (not is_dir and want == "dir"):
            continue
        if inc(rel) and not exc(rel):
            hits.append(rel + ("/" if is_dir else ""))
    shown = hits[:limit]
    return "\n".join(shown) + f"\n\n---\nTotal matches: {len(hits)}\n"


def grep_search(path, pattern, include=None, exclude=None, return_line_numbers=False,
                literal=False, ignore_case=False, context_lines=0):
    target = _path(path)
    try:
        rx = re.compile(re.escape(pattern) if literal else pattern,
                        re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return _err(f"bad pattern: {e}")
    if os.path.isfile(target):
        files = [(target, target)]
    elif os.path.isdir(target):
        inc, exc = _matcher(include), (_matcher(exclude) if exclude else lambda rel: False)
        files = [(os.path.join(target, rel), rel) for rel, is_dir in _walk(target)
                 if not is_dir and inc(rel) and not exc(rel)]
    else:
        return _err(f"cannot stat: {target}")
    ctx = int(context_lines or 0)
    out, total = [], 0
    for full, shown in files:
        try:
            with open(full, "rb") as f:
                data = f.read(2_000_000)
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue                                   # binary file
        lines = data.decode(errors="replace").splitlines()
        matches = [i for i, line in enumerate(lines) if rx.search(line)]
        total += len(matches)
        if not ctx:
            for i in matches:
                out.append(f"{shown}:{i + 1}:{lines[i]}" if return_line_numbers
                           else f"{shown}:{lines[i]}")
            continue
        for i in matches:
            for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
                sep = ":" if j == i else "-"
                out.append(f"{shown}{sep}{j + 1}{sep}{lines[j]}")
            out.append("--")
        if len(out) > 400:
            break
    body = "\n".join(out[:400]) + ("\n[... more matches cut ...]" if len(out) > 400 else "")
    return body + f"\n\n---\nTotal matches: {total}\n"


def exec_shell_command(command, timeout=10, max_output_size=16384):
    timeout = max(1, min(int(timeout or 10), 60))
    try:
        r = subprocess.run(command, shell=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        out, tail = r.stdout, f"\n[exit code: {r.returncode}]"
    except subprocess.TimeoutExpired as e:
        out, tail = e.stdout or b"", f"\n[timed out after {timeout} s]"
    limit = int(max_output_size or 16384)
    text = out[:limit].decode(errors="replace")
    if len(out) > limit:
        text += f"\n[... output cut at {limit} bytes ...]"
    return text + tail


def write_file(path, content):
    p = _path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)
    return json.dumps({"result": "file written successfully", "path": p,
                       "bytes": len(content.encode())})


def edit_file(path, edits):
    p = _path(path)
    try:
        with open(p) as f:
            text = f.read()
    except OSError:
        return _err(f"cannot read file: {p}")
    spans = []
    for n, e in enumerate(edits or []):
        old = e.get("old_text", "")
        count = text.count(old) if old else 0
        if count == 0:
            return _err(f"could not find edits[{n}].old_text in {p}, it must match "
                        "the file's current content exactly")
        if count > 1:
            return _err(f"edits[{n}].old_text is not unique in {p}; add more surrounding text")
        start = text.index(old)
        spans.append((start, start + len(old), e.get("new_text", "")))
    spans.sort()
    for (s1, e1, _), (s2, _, _) in zip(spans, spans[1:]):
        if s2 < e1:
            return _err("edits overlap; merge nearby changes into one edit")
    for start, end, new in reversed(spans):
        text = text[:start] + new + text[end:]
    with open(p, "w") as f:
        f.write(text)
    return json.dumps({"result": "file edited successfully", "path": p,
                       "edits_applied": len(spans)})


def get_info():
    try:
        uname = subprocess.run(["uname", "-a"], capture_output=True, text=True).stdout.strip()
    except OSError:
        u = os.uname()
        uname = f"{u.sysname} {u.nodename} {u.release} {u.version} {u.machine}"
    # cwd is pi-code's own folder: relative paths in these tools resolve there
    return json.dumps({"os": uname, "cwd": os.getcwd()})


_IMPL = {"read_file": read_file, "file_glob_search": file_glob_search,
         "grep_search": grep_search, "exec_shell_command": exec_shell_command,
         "write_file": write_file, "edit_file": edit_file, "get_info": get_info}


def call(name, args):
    try:
        return _IMPL[name](**args)
    except TypeError as e:                 # wrong/missing arguments
        return _err(str(e))
