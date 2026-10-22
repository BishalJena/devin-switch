import json
import os
import shlex
import subprocess
import sys
import urllib.parse
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


def test_gui_chats_with_panels_route_canvases_beside_the_chat(monkeypatch) -> None:
    install_extension()
    assert panels.canvas_target(CANVAS) == CANVAS, "terminal launches keep the browser"
    monkeypatch.setenv("DS_ACP_RUN_ID", "run-1")
    target = urllib.parse.urlsplit(panels.canvas_target(CANVAS))
    assert (target.scheme, target.netloc, target.path) == (
        "devin",
        "devin-switch.panels",
        "/canvas",
    )
    assert urllib.parse.parse_qs(target.query) == {"url": [CANVAS]}


def test_gui_chats_without_panels_fall_back_to_the_browser(monkeypatch) -> None:
    monkeypatch.setenv("DS_ACP_RUN_ID", "run-1")
    assert panels.canvas_target(CANVAS) == CANVAS


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:1/c/x",
        "http://example.com/c/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_only_local_http_canvas_urls_are_shown(url: str, monkeypatch) -> None:
    install_extension()
    monkeypatch.setenv("DS_ACP_RUN_ID", "run-1")
    with pytest.raises(SwitchError):
        panels.canvas_target(url)


def test_canvas_show_command_opens_without_focus_and_rejects_foreign_urls(
    opener: Path, monkeypatch, store
) -> None:
    install_extension()
    environment = {**os.environ, "DS_ACP_RUN_ID": "run-1"}
    run = lambda url: subprocess.run(  # noqa: E731
        (sys.executable, "-m", "devin_switch.cli", "canvas", "show", url),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    shown = run(CANVAS)
    assert shown.returncode == 0, shown.stderr
    assert "beside the chat" in shown.stdout
    args = json.loads(opener.read_text())
    assert args[0] == "-g" and args[1].startswith("devin://devin-switch.panels/canvas?url=")
    refused = run("http://evil.example/c/x")
    assert refused.returncode != 0 and "Only local http" in refused.stderr
    assert len(opener.read_text().splitlines()) == 1


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
