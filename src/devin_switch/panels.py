"""Desktop side panels: the live Excalidraw canvas and the agent browser, beside the chat."""

import json
import os
import shlex
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from devin_switch.executable import command_prefix
from devin_switch.store import Store, SwitchError, private_directory, write_json

PUBLISHER = "devin-switch"
NAME = "panels"
EXTENSION_ID = f"{PUBLISHER}.{NAME}"
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


def check_canvas_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise SwitchError("Only local http canvas URLs can be shown.")


def request_panel(store: Store, url: str) -> bool:
    """Ask the Desktop window showing this folder to open the canvas; False if none claims it."""
    folder = store.root / "panels/requests"
    for path in (store.root / "panels", folder):
        private_directory(path)
    request = folder / f"{time.time_ns()}-{os.getpid()}.json"
    write_json(request, {"url": url, "cwd": os.getcwd(), "at": int(time.time() * 1000)})
    deadline = time.monotonic() + float(os.environ.get("DS_PANEL_TIMEOUT") or 3)
    while time.monotonic() < deadline:
        if not request.exists():
            return True
        time.sleep(0.05)
    try:
        request.unlink()
    except FileNotFoundError:
        return True  # claimed at the last moment
    return False


def show_canvas(store: Store, url: str) -> str:
    check_canvas_url(url)
    gui = os.environ.get("DS_ACP_RUN_ID") and installed_version() is not None
    if gui and request_panel(store, url):
        return "Opened the canvas beside the chat."
    opener = os.environ.get("DS_OPEN") or "/usr/bin/open"
    # -g opens the browser without stealing focus from the chat.
    subprocess.run((opener, "-g", url), check=True, timeout=15, stdin=subprocess.DEVNULL)
    return "Opened the canvas in the browser."


SOURCE = Path(__file__).parent / "desktop_extension"
DESKTOP_CLI = Path("/Applications/Devin.app/Contents/Resources/app/bin/devin-desktop")
CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\
<Default Extension=".json" ContentType="application/json"/>\
<Default Extension=".js" ContentType="application/javascript"/>\
<Default Extension=".css" ContentType="text/css"/>\
<Default Extension=".vsixmanifest" ContentType="text/xml"/></Types>
"""


def manifest() -> dict:
    return json.loads((SOURCE / "package.json").read_text())


def vsix_manifest(package: dict) -> str:
    field = lambda key: escape(str(package[key]), {'"': "&quot;"})  # noqa: E731
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{field("name")}" Version="{field("version")}" \
Publisher="{field("publisher")}"/>
    <DisplayName>{field("displayName")}</DisplayName>
    <Description xml:space="preserve">{field("description")}</Description>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" \
Value="{escape(package["engines"]["vscode"])}"/>
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" \
Addressable="true"/>
  </Assets>
</PackageManifest>
"""


def build_vsix(destination: Path, home: Path) -> Path:
    """Package the extension with this ds's command line so it never guesses a PATH."""
    package = manifest()
    vsix = destination / f"{EXTENSION_ID}-{package['version']}.vsix"
    with zipfile.ZipFile(vsix, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("extension.vsixmanifest", vsix_manifest(package))
        for path in sorted(SOURCE.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, f"extension/{path.relative_to(SOURCE).as_posix()}")
        archive.writestr(
            "extension/ds.json", json.dumps({"command": command_prefix(), "home": str(home)})
        )
    return vsix


def desktop_cli() -> Path:
    cli = Path(os.environ.get("DS_DESKTOP_CLI") or DESKTOP_CLI)
    if not cli.exists():
        raise SwitchError(f"The Desktop app's command line tool is missing: {cli}")
    return cli


def install(store: Store) -> str:
    cli = desktop_cli()
    with tempfile.TemporaryDirectory(prefix="ds-panels-") as temporary:
        vsix = build_vsix(Path(temporary), store.root)
        result = subprocess.run(
            (str(cli), "--install-extension", str(vsix), "--force"),
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
    if result.returncode:
        raise SwitchError(
            f"Installing the panels failed: {(result.stderr or result.stdout).strip()}"
        )
    version = manifest()["version"]
    if installed_version() != version:
        raise SwitchError("The Desktop app did not record the panels extension; see its log.")
    return "\n".join(
        (
            f"Installed the Switch panels {version} into the Desktop app.",
            "Commands: 'Switch: Open Excalidraw Canvas' and 'Switch: Open Agent Browser'.",
            "Canvases from GUI chats now open beside the chat; terminal chats keep the browser.",
            "If the commands are missing, run 'Developer: Reload Window' once.",
        )
    )


def status() -> str:
    packaged, current = manifest()["version"], installed_version()
    if current is None:
        return f"Switch panels are not installed (available: {packaged}). Run: ds panels install"
    note = "" if current == packaged else f"; {packaged} is available, run: ds panels install"
    return f"Switch panels {current} are installed in the Desktop app{note}."


def command(args, store: Store) -> int:
    if args.action == "show":
        print(show_canvas(store, args.url))
    elif args.action == "install":
        print(install(store))
    elif args.action == "status":
        print(status())
    return 0
