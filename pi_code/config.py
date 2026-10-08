"""pi-code settings, all from environment variables (see pi_code/__init__.py).

Other modules read these as config.NAME at call time, so a test or main() can
change them (main() sets TOOL_PICKER from --picker)."""
import os
import time

BASE_URL = os.environ.get("PI_CODE_URL", "http://localhost:8091")
DEFAULT_MODEL = os.environ.get("PI_CODE_DEFAULT_MODEL", "chat")
VISION_MODEL = os.environ.get("PI_CODE_VISION_MODEL", "lfm-vision")
AUDIO_MODEL = os.environ.get("PI_CODE_AUDIO_MODEL", "asr")
VIDEO_MODEL = os.environ.get("PI_CODE_VIDEO_MODEL", "lfm-vision-1.6b")
DEFAULT_TIMEOUT = int(os.environ.get("PI_CODE_TIMEOUT", "900"))
START_HINT = os.environ.get(
    "PI_CODE_START_HINT", "the router server for your llama.cpp setup")
MAX_ROUNDS = int(os.environ.get("PI_CODE_MAX_ROUNDS", "10"))
# Failed tool calls in a row (or the same call repeated) before a turn stops.
MAX_FAILURES = max(1, int(os.environ.get("PI_CODE_MAX_FAILURES", "3")))
MAX_TOKENS = int(os.environ.get("PI_CODE_MAX_TOKENS", "1024"))
CONTEXT_CHARS = int(os.environ.get("PI_CODE_CONTEXT_CHARS", "12000"))
TOOL_CHARS = int(os.environ.get("PI_CODE_TOOL_CHARS", "2500"))
FULL_TOOL_DOCS = os.environ.get("PI_CODE_FULL_TOOL_DOCS", "") not in ("", "0")
TOOL_PICKER = os.environ.get("PI_CODE_TOOL_PICKER", "laya")
# Per-model settings measured by `pi-code --tune` (tune.py).
TUNED_DIR = os.path.expanduser(os.environ.get(
    "PI_CODE_TUNED_DIR", "~/.config/pi-code/tuned"))
RESULTS_DIR = os.path.join(os.path.expanduser("~/.cache/pi-code/results"),
                           time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")

# Plug-in folders (see plugins.py)
ADAPTERS_DIR = os.path.expanduser(os.environ.get(
    "PI_CODE_ADAPTERS", "~/.config/pi-code/adapters"))
PICKERS_DIR = os.path.expanduser(os.environ.get(
    "PI_CODE_PICKERS", "~/.config/pi-code/pickers"))
MODEL_ADAPTERS_DIR = os.path.expanduser(os.environ.get(
    "PI_CODE_MODEL_ADAPTERS", "~/.config/pi-code/models"))
SOURCES_DIR = os.path.expanduser(os.environ.get(
    "PI_CODE_SOURCES_DIR", "~/.config/pi-code/sources"))

# The model's instructions: PI_CODE_SYSTEM_PROMPT / PI_CODE_PLAIN_PROMPT text,
# else system.txt / plain.txt in PROMPTS_DIR, else the built-in ones (agent.py).
PROMPTS_DIR = os.path.expanduser(os.environ.get(
    "PI_CODE_PROMPTS_DIR", "~/.config/pi-code/prompts"))
SYSTEM_PROMPT = os.environ.get("PI_CODE_SYSTEM_PROMPT")
PLAIN_PROMPT = os.environ.get("PI_CODE_PLAIN_PROMPT")
