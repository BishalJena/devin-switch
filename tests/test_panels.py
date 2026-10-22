import json
import os
import shlex
import subprocess
import sys
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


def test_native_children_get_the_chat_id_command_unless_one_is_set(
    native: Native, monkeypatch
) -> None:
    account = native.store.accounts()[0]
    command = shlex.split(native.environment(account)["WHITEBOARD_CHAT_COMMAND"])
    assert command == [sys.executable, "-m", "devin_switch.cli", "chat", "id"]
    assert "WHITEBOARD_OPEN" not in native.environment(account)
    monkeypatch.setenv("WHITEBOARD_CHAT_COMMAND", "echo fixed")
    assert native.environment(account)["WHITEBOARD_CHAT_COMMAND"] == "echo fixed"


def test_chat_id_command_survives_paths_with_spaces(monkeypatch) -> None:
    from devin_switch.executable import ds_command

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Devin Switch.app/Contents/ds")
    assert shlex.split(ds_command("chat", "id")) == [
        "/Applications/Devin Switch.app/Contents/ds",
        "chat",
        "id",
    ]


def test_chat_id_outside_a_gui_chat_fails_without_guessing(store) -> None:
    done = subprocess.run(
        (sys.executable, "-m", "devin_switch.cli", "chat", "id"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode != 0 and done.stdout == ""
    assert "Not inside a Desktop chat launched through Switch" in done.stderr
    stale = subprocess.run(
        (sys.executable, "-m", "devin_switch.cli", "chat", "id"),
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "DS_ACP_RUN_ID": "not-a-live-run"},
    )
    assert stale.returncode != 0 and stale.stdout == ""


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
