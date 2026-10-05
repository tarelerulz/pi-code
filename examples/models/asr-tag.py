"""pi-code model adapter: strip the "language English<asr_text>" prefix that
Qwen3-ASR-style models put before the transcript (moved out of pi-code
2026-10-04; used to be built in).

A model adapter is a .py file in ~/.config/pi-code/models/
(PI_CODE_MODEL_ADAPTERS) with, all optional:
  MODELS        preset names it applies to (missing or "*" = every model)
  hold(text)    True = don't print the start of the reply yet
  clean(text)   tidy the start of the reply before it is printed and kept
"""
TAG = "<asr_text>"


def hold(text):
    # wait until we know whether the tag is coming (it is within ~30 chars)
    return TAG not in text and "<" in text and len(text) < 40


def clean(text):
    return text.split(TAG, 1)[1] if TAG in text else text
