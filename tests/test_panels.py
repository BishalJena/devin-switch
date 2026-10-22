import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from devin_switch import panels
from devin_switch.native import Native
from devin_switch.store import SwitchError

CANVAS = "http://127.0.0.1:47428/c/repo-abc123?token=t0k"


def install_extension(version: str = "0.1.0") -> None:
    folder = panels.extensions_directory()
    folder.mkdir(parents=True, exist_ok=True)
    entries = [
        {"identifier": {"id": "ms-python.python"}, "version": "1"},
        {"identifier": {"id": "Devin-Switch.Panels"}, "version": version},
    ]
    (folder / "extensions.json").write_text(json.dumps(entries))


@pytest.fixture
def opener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "opened"
    script = tmp_path / "fake-open"
    script.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    script.chmod(0o700)
    monkeypatch.setenv("DS_OPEN", str(script))
    return log


def test_installed_version_reads_desktop_extensions_case_insensitively() -> None:
    assert panels.installed_version() is None
    install_extension("0.2.0")
    assert panels.installed_version() == "0.2.0"
    (panels.extensions_directory() / "extensions.json").write_text("{broken")
    assert panels.installed_version() is None


def requests_folder(store) -> Path:
    return store.root / "panels/requests"


def claim_next(store, claimed: list) -> threading.Thread:
    """Plays a Desktop window: claims the first request it sees, like the extension does."""

    def run() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for path in requests_folder(store).glob("*.json"):
                claimed.append(json.loads(path.read_text()))
                path.unlink()
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_gui_chats_with_panels_hand_the_canvas_to_the_window_of_their_folder(
    store, opener: Path, monkeypatch, tmp_path: Path
) -> None:
    install_extension()
    monkeypatch.setenv("DS_ACP_RUN_ID", "run-1")
    monkeypatch.chdir(tmp_path)
    claimed: list = []
    window = claim_next(store, claimed)
    assert panels.show_canvas(store, CANVAS) == "Opened the canvas beside the chat."
    window.join(5)
    assert claimed[0]["url"] == CANVAS and claimed[0]["cwd"] == str(tmp_path)
    assert abs(claimed[0]["at"] / 1000 - time.time()) < 30
    assert not opener.exists(), "no browser tab when a window took it"
    assert oct(requests_folder(store).stat().st_mode & 0o777) == "0o700"


def test_unclaimed_requests_fall_back_to_the_browser_and_leave_nothing_behind(
    store, opener: Path, monkeypatch
) -> None:
    install_extension()
    monkeypatch.setenv("DS_ACP_RUN_ID", "run-1")
    monkeypatch.setenv("DS_PANEL_TIMEOUT", "0.2")
    assert panels.show_canvas(store, CANVAS) == "Opened the canvas in the browser."
    assert json.loads(opener.read_text()) == ["-g", CANVAS]
    assert list(requests_folder(store).glob("*")) == []


@pytest.mark.parametrize("gui, panels_installed", [(False, True), (True, False)])
def test_terminal_chats_or_missing_panels_use_the_browser_directly(
    store, opener: Path, monkeypatch, gui: bool, panels_installed: bool
) -> None:
    if panels_installed:
        install_extension()
    if gui:
        monkeypatch.setenv("DS_ACP_RUN_ID", "run-1")
    assert panels.show_canvas(store, CANVAS) == "Opened the canvas in the browser."
    assert json.loads(opener.read_text()) == ["-g", CANVAS]
    assert not requests_folder(store).exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:1/c/x",
        "http://example.com/c/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_only_local_http_canvas_urls_are_shown(url: str, store, opener: Path, monkeypatch) -> None:
    install_extension()
    monkeypatch.setenv("DS_ACP_RUN_ID", "run-1")
    with pytest.raises(SwitchError):
        panels.show_canvas(store, url)
    assert not opener.exists() and not requests_folder(store).exists()


def test_canvas_show_command_hands_off_and_rejects_foreign_urls(opener: Path, store) -> None:
    install_extension()
    environment = {**os.environ, "DS_ACP_RUN_ID": "run-1"}
    run = lambda url: subprocess.run(  # noqa: E731
        (sys.executable, "-m", "devin_switch.cli", "canvas", "show", url),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    claimed: list = []
    claim_next(store, claimed)
    shown = run(CANVAS)
    assert shown.returncode == 0, shown.stderr
    assert "beside the chat" in shown.stdout and claimed[0]["url"] == CANVAS
    refused = run("http://evil.example/c/x")
    assert refused.returncode != 0 and "Only local http" in refused.stderr
    assert not opener.exists()


def test_native_children_get_the_canvas_opener_unless_the_user_chose_one(
    native: Native, monkeypatch
) -> None:
    account = native.store.accounts()[0]
    template = native.environment(account)[panels.OPEN_ENVIRONMENT]
    argv = shlex.split(template.replace("{url}", shlex.quote(CANVAS)))
    assert argv[-3:] == ["canvas", "show", CANVAS]
    monkeypatch.setenv(panels.OPEN_ENVIRONMENT, "none")
    assert native.environment(account)[panels.OPEN_ENVIRONMENT] == "none"


def test_opener_template_survives_paths_with_spaces(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Devin Switch.app/Contents/ds")
    template = panels.opener_command()
    assert shlex.split(template) == [
        "/Applications/Devin Switch.app/Contents/ds",
        "canvas",
        "show",
        "{url}",
    ]


FAKE_DESKTOP_CLI = """\
import json, os, pathlib, sys, zipfile
args = sys.argv[1:]
if os.environ.get("FAKE_DESKTOP_FAIL"):
    print("boom: corrupt package", file=sys.stderr)
    sys.exit(3)
assert args[0] == "--install-extension" and args[2] == "--force", args
root = pathlib.Path(os.environ["DS_DESKTOP_EXTENSIONS"])
with zipfile.ZipFile(args[1]) as archive:
    package = json.loads(archive.read("extension/package.json"))
    folder = root / f"{package['publisher']}.{package['name']}-{package['version']}"
    for name in archive.namelist():
        if name.startswith("extension/"):
            target = folder / name.removeprefix("extension/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
if not os.environ.get("FAKE_DESKTOP_FORGET"):
    entry = {"identifier": {"id": f"{package['publisher']}.{package['name']}"},
             "version": package["version"]}
    (root / "extensions.json").write_text(json.dumps([entry]))
"""


@pytest.fixture
def desktop_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cli = tmp_path / "devin-desktop"
    cli.write_text(f"#!{sys.executable}\n{FAKE_DESKTOP_CLI}")
    cli.chmod(0o700)
    monkeypatch.setenv("DS_DESKTOP_CLI", str(cli))
    panels.extensions_directory().mkdir(parents=True, exist_ok=True)
    return cli


def test_vsix_holds_every_extension_file_a_valid_manifest_and_this_ds(tmp_path: Path) -> None:
    import xml.etree.ElementTree as ET
    import zipfile

    vsix = panels.build_vsix(tmp_path, Path("/state/ds"))
    with zipfile.ZipFile(vsix) as archive:
        names = set(archive.namelist())
        package = json.loads(archive.read("extension/package.json"))
        identity = ET.fromstring(archive.read("extension.vsixmanifest")).find(".//{*}Identity")
        ds = json.loads(archive.read("extension/ds.json"))
    sources = {
        f"extension/{p.relative_to(panels.SOURCE).as_posix()}"
        for p in panels.SOURCE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert sources <= names and {"[Content_Types].xml", "extension/ds.json"} <= names
    assert {"extension/extension.js", "extension/media/browser-view.js"} <= names
    assert identity.attrib == {
        "Language": "en-US",
        "Id": package["name"],
        "Version": package["version"],
        "Publisher": package["publisher"],
    }
    assert f"{package['publisher']}.{package['name']}" == panels.EXTENSION_ID
    assert ds == {"command": [sys.executable, "-m", "devin_switch.cli"], "home": "/state/ds"}
    assert vsix.name == f"{panels.EXTENSION_ID}-{package['version']}.vsix"


def test_install_and_status_report_the_desktop_extension(desktop_cli: Path, store) -> None:
    version = panels.manifest()["version"]
    assert "not installed" in panels.status()
    assert f"Installed the Switch panels {version}" in panels.install(store)
    assert panels.installed_version() == version
    assert panels.status() == f"Switch panels {version} are installed in the Desktop app."
    installed = panels.extensions_directory() / f"{panels.EXTENSION_ID}-{version}"
    ds = json.loads((installed / "ds.json").read_text())
    assert ds["command"][0] == sys.executable and ds["home"] == str(store.root)
    install_extension("0.0.1")
    assert "is available, run: ds panels install" in panels.status()


def test_install_surfaces_desktop_failures(desktop_cli: Path, monkeypatch, store) -> None:
    monkeypatch.setenv("FAKE_DESKTOP_FAIL", "1")
    with pytest.raises(SwitchError, match="boom: corrupt package"):
        panels.install(store)
    monkeypatch.delenv("FAKE_DESKTOP_FAIL")
    monkeypatch.setenv("FAKE_DESKTOP_FORGET", "1")
    with pytest.raises(SwitchError, match="did not record"):
        panels.install(store)
    monkeypatch.setenv("DS_DESKTOP_CLI", "/nonexistent/devin-desktop")
    with pytest.raises(SwitchError, match="command line tool is missing"):
        panels.install(store)


@pytest.mark.skipif(not panels.DESKTOP_CLI.exists(), reason="The Desktop app is not installed")
def test_real_desktop_cli_accepts_the_vsix_in_an_isolated_extensions_dir(tmp_path: Path) -> None:
    extensions, user_data = tmp_path / "extensions", tmp_path / "user-data"
    vsix = panels.build_vsix(tmp_path, tmp_path / "state")
    environment = {
        k: v for k, v in os.environ.items() if not k.startswith(("VSCODE_", "ELECTRON_"))
    }
    result = subprocess.run(
        (
            str(panels.DESKTOP_CLI),
            "--extensions-dir",
            str(extensions),
            "--user-data-dir",
            str(user_data),
            "--install-extension",
            str(vsix),
            "--force",
        ),
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DS_DESKTOP_EXTENSIONS", str(extensions))
    try:
        assert panels.installed_version() == panels.manifest()["version"]
    finally:
        monkeypatch.undo()
