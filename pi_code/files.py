"""File attachments (-f): detect the kind of file, pick a preset, and build
OpenAI-compatible multimodal message content."""
import base64
import mimetypes
import os
import subprocess

from . import config


def detect_mime(path):
    """Inspect file contents; use the filename only if `file` cannot identify it."""
    try:
        result = subprocess.run(
            ["file", "--brief", "--mime-type", "--", path],
            check=True, capture_output=True, text=True)
        mime = result.stdout.strip()
        if mime and mime != "application/octet-stream":
            return mime
    except (OSError, subprocess.CalledProcessError):
        pass
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def file_modality(path):
    mime = detect_mime(path)
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("text/") or mime in (
            "application/json", "application/xml", "application/toml",
            "application/yaml", "application/x-yaml"):
        return "text"
    return "unknown"


def choose_model(paths):
    """Choose a preset deterministically from attached-file modalities."""
    modalities = {file_modality(path) for path in paths}
    modalities.discard("text")  # text can accompany any other modality
    if "unknown" in modalities:
        raise ValueError("cannot automatically choose a model for an unknown file type")
    if len(modalities) > 1:
        raise ValueError("mixed image/audio/video input needs an explicit -m MODEL")
    if not modalities:
        return config.DEFAULT_MODEL
    return {"image": config.VISION_MODEL, "audio": config.AUDIO_MODEL,
            "video": config.VIDEO_MODEL}[next(iter(modalities))]


def user_content(text, paths):
    """Build OpenAI-compatible multimodal content from local files."""
    if not paths:
        return text
    parts = []
    for path in paths:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        mime = detect_mime(path)
        encoded = base64.b64encode(data).decode("ascii")
        if mime.startswith("image/"):
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{mime};base64,{encoded}"}})
        elif mime.startswith("audio/"):
            parts.append({"type": "input_audio", "input_audio": {
                "data": encoded, "format": os.path.splitext(path)[1].lstrip(".")}})
        elif mime.startswith("video/"):
            parts.append({"type": "input_video", "input_video": {
                "data": encoded}})
        elif mime.startswith("text/") or path.lower().endswith(
                (".md", ".json", ".yaml", ".yml", ".toml", ".py", ".c", ".h")):
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{path} is not valid UTF-8 text") from exc
            parts.append({"type": "text", "text":
                          f"File: {os.path.basename(path)}\n\n{content}"})
        else:
            raise ValueError(
                f"unsupported file type for {path}; use an image, audio, video, or text file")
    parts.append({"type": "text", "text": text or "Describe and analyze the attached file."})
    return parts
