import http.server
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
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


@pytest.mark.skipif(
    os.environ.get("DS_TEST_DESKTOP") != "1" or not DESKTOP.exists() or not CHROME.exists(),
    reason="Opens a real, isolated Desktop window; set DS_TEST_DESKTOP=1 to run",
)
def test_real_desktop_opens_the_canvas_request_and_watches_the_agent_browser(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from devin_switch import agent_browser, browser, panels

    seen: list[str] = []

    class Canvas(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            body = b"<!doctype html><title>canvas</title><script>fetch('/loaded')</script>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Canvas)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    chrome_data = tmp_path / "chrome"
    (chrome_data / "Profile 1").mkdir(parents=True)
    profiles = {"Profile 1": {"name": "Test", "user_name": "panel@example.invalid"}}
    (chrome_data / "Local State").write_text(json.dumps({"profile": {"info_cache": profiles}}))
    monkeypatch.setattr(browser, "CHROME_DATA", chrome_data)
    monkeypatch.setenv("DS_CHROME_DATA", str(chrome_data))
    monkeypatch.setenv("DS_BROWSER_CHROME", str(CHROME))
    chat = agent_browser.Lease(store)
    chat.acquire(browser.profiles()[0])
    port = agent_browser.running(store, "Profile 1")["port"]

    extension = tmp_path / "extension"
    with zipfile.ZipFile(panels.build_vsix(tmp_path, store.root)) as archive:
        for name in archive.namelist():
            if name.startswith("extension/"):
                target = extension / name.removeprefix("extension/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_file = tmp_path / "result.json"
    canvas = f"http://127.0.0.1:{server.server_address[1]}/c/test?token=t"
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("VSCODE_", "ELECTRON_"))},
        "DS_HOME": str(store.root),
        "PANELS_CANVAS_URL": canvas,
        "PANELS_CHROME_PORT": str(port),
        "PANELS_RESULT": str(result_file),
    }
    # Electron's IPC socket path must stay under 104 characters; pytest's tmp_path is longer.
    short = tempfile.mkdtemp(prefix="dsp-", dir="/tmp")
    try:
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
            env=environment,
            timeout=150,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result_file.exists(), (launched.stdout + launched.stderr)[-3000:]
        result = json.loads(result_file.read_text())
        assert "error" not in result, result["error"]
        assert result["claimed"] and result["canvasTab"] and result["browserTab"]
        assert any(path.startswith("/c/test?") and "embed=1" in path for path in seen), seen
        assert "/loaded" in seen, "the canvas page ran inside the panel's iframe"
        assert result["browser"]["attached"] >= 1
    finally:
        chat.release()
        server.shutdown()
        shutil.rmtree(short, ignore_errors=True)
        if current := agent_browser.state(store, "Profile 1"):
            agent_browser.stop(current)
