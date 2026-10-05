"""pi-code adapter for the llama.cpp router's built-in file and shell tools.

CONFIRM lists the tools that change things or run commands, so pi-code asks
"run? [Y/n]" before them (moved out of pi-code 2026-10-04; same set as before).
The read-only ones run without asking.
"""
TOOLS = ["read_file", "file_glob_search", "grep_search", "exec_shell_command",
         "write_file", "edit_file", "apply_diff", "get_info"]
CONFIRM = ["exec_shell_command", "write_file", "edit_file", "apply_diff"]


def summary(name, args):
    if name == "exec_shell_command":
        return args.get("command", "")
    if name in ("write_file", "edit_file", "apply_diff", "read_file"):
        return args.get("path", "")
    return None      # pi-code shows the arguments
