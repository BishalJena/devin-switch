import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from devin_switch.store import Store

TESTS = sorted((Path(__file__).parent / "desktop_extension").glob("*.test.js"))


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is not installed")
def test_desktop_extension_node_suite() -> None:
    """The Desktop panels extension is plain JavaScript; its tests run under node:test."""
    assert TESTS
    result = subprocess.run(
        ("node", "--test", *map(str, TESTS)), capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


DESKTOP = Path("/Applications/Devin.app/Contents/MacOS/Devin")
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
WHITEBOARD = shutil.which("whiteboard-mcp")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.skipif(
    os.environ.get("DS_TEST_DESKTOP") != "1"
    or not DESKTOP.exists()
    or not CHROME.exists()
    or not WHITEBOARD,
    reason="Opens a real, isolated Desktop window with whiteboard-mcp; set DS_TEST_DESKTOP=1",
)
def test_real_desktop_shows_whiteboards_beside_the_chat_and_watches_the_agent_browser(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from devin_switch import agent_browser, browser, panels

    # Electron's IPC socket path must stay under 104 characters; pytest's tmp_path is longer.
    short = tempfile.mkdtemp(prefix="dsp-", dir="/tmp")
    home = Path(short) / "wb"
    whiteboard_env = {
        **os.environ,
        "WHITEBOARD_HOME": str(home),
        "WHITEBOARD_PORT": str(free_port()),
        "WHITEBOARD_IDLE_MS": "0",
    }
    daemon = subprocess.Popen((WHITEBOARD, "serve"), env=whiteboard_env, stdin=subprocess.DEVNULL)
    chat = agent_browser.Lease(store)
    try:
        deadline = time.monotonic() + 20
        while not (home / "daemon.json").exists():
            assert time.monotonic() < deadline, "whiteboard daemon did not start"
            time.sleep(0.1)
        port = json.loads((home / "daemon.json").read_text())["port"]
        token = (home / "token").read_text().strip()

        chrome_data = tmp_path / "chrome"
        (chrome_data / "Profile 1").mkdir(parents=True)
        profiles = {"Profile 1": {"name": "Test", "user_name": "panel@example.invalid"}}
        (chrome_data / "Local State").write_text(json.dumps({"profile": {"info_cache": profiles}}))
        monkeypatch.setattr(browser, "CHROME_DATA", chrome_data)
        monkeypatch.setenv("DS_CHROME_DATA", str(chrome_data))
        monkeypatch.setenv("DS_BROWSER_CHROME", str(CHROME))
        chat.acquire(browser.profiles()[0])
        chrome_port = agent_browser.running(store, "Profile 1")["port"]

        extension = tmp_path / "extension"
        with zipfile.ZipFile(panels.build_vsix(tmp_path)) as archive:
            for name in archive.namelist():
                if name.startswith("extension/"):
                    target = extension / name.removeprefix("extension/")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        result_file = tmp_path / "result.json"
        inherited = {
            k: v for k, v in os.environ.items() if not k.startswith(("VSCODE_", "ELECTRON_"))
        }
        launched = subprocess.run(
            (
                str(DESKTOP),
                f"--extensionDevelopmentPath={extension}",
                f"--extensionTestsPath={Path(__file__).parent / 'desktop_extension/integration'}",
                f"--user-data-dir={short}/u",
                f"--extensions-dir={short}/e",
                "--disable-workspace-trust",
                "--skip-welcome",
                "--new-window",
                str(workspace),
            ),
            env={
                **inherited,
                "DS_HOME": str(store.root),
                "WHITEBOARD_HOME": str(home),
                "PANELS_WB_URL": f"http://127.0.0.1:{port}",
                "PANELS_WB_TOKEN": token,
                "PANELS_CHROME_PORT": str(chrome_port),
                "PANELS_RESULT": str(result_file),
            },
            timeout=150,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result_file.exists(), (launched.stdout + launched.stderr)[-3000:]
        result = json.loads(result_file.read_text())
        assert "error" not in result, result["error"]
        assert result["shown"] and result["canvasTab"] and result["viewers"] >= 1
        assert result["browserTab"] and result["browser"]["attached"] >= 1
    finally:
        chat.release()
        daemon.terminate()
        daemon.wait(timeout=15)
        shutil.rmtree(short, ignore_errors=True)
        if current := agent_browser.state(store, "Profile 1"):
            agent_browser.stop(current)
