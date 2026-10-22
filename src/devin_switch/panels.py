"""Desktop side panels: the live Excalidraw canvas and the agent browser, beside the chat."""

import json
import os
import shlex
import subprocess
import urllib.parse
from pathlib import Path

from devin_switch.executable import command_prefix
from devin_switch.store import SwitchError

PUBLISHER = "devin-switch"
NAME = "panels"
EXTENSION_ID = f"{PUBLISHER}.{NAME}"
URL_SCHEME = "devin"
OPEN_ENVIRONMENT = "EXCALIDRAW_LIVE_OPEN"


def extensions_directory() -> Path:
    return Path(os.environ.get("DS_DESKTOP_EXTENSIONS") or Path.home() / ".devin/extensions")


def installed_version() -> str | None:
    try:
        entries = json.loads((extensions_directory() / "extensions.json").read_text())
    except (OSError, ValueError):
        return None
    for entry in entries if isinstance(entries, list) else ():
        identifier = entry.get("identifier", {}) if isinstance(entry, dict) else {}
        if str(identifier.get("id", "")).lower() == EXTENSION_ID:
            return str(entry.get("version") or "")
    return None


def opener_command() -> str:
    """The `{url}` template the Excalidraw MCP runs to show a canvas."""
    return f"{shlex.join([*command_prefix(), 'canvas', 'show'])} {{url}}"


def canvas_target(url: str) -> str:
    """Where to send a canvas URL: beside a Desktop chat when possible, else the browser."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise SwitchError("Only local http canvas URLs can be shown.")
    if os.environ.get("DS_ACP_RUN_ID") and installed_version() is not None:
        query = urllib.parse.urlencode({"url": url})
        return f"{URL_SCHEME}://{EXTENSION_ID}/canvas?{query}"
    return url


def show_canvas(url: str) -> str:
    target = canvas_target(url)
    opener = os.environ.get("DS_OPEN") or "/usr/bin/open"
    # -g delivers the URL without stealing focus from the chat.
    subprocess.run((opener, "-g", target), check=True, timeout=15, stdin=subprocess.DEVNULL)
    return "Opened the canvas beside the chat." if target != url else "Opened the canvas."


def command(args) -> int:
    if args.action == "show":
        print(show_canvas(args.url))
    return 0
