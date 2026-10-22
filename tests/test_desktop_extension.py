import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = sorted((Path(__file__).parent / "desktop_extension").glob("*.test.js"))


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is not installed")
def test_desktop_extension_node_suite() -> None:
    """The Desktop panels extension is plain JavaScript; its tests run under node:test."""
    assert TESTS
    result = subprocess.run(
        ("node", "--test", *map(str, TESTS)), capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
