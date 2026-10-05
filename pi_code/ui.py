"""Terminal text styling (plain text when not on a terminal)."""
import sys


def dim(s):
    return f"\033[2m{s}\033[0m" if sys.stdout.isatty() else s


def bold(s):
    return f"\033[1m{s}\033[0m" if sys.stdout.isatty() else s
