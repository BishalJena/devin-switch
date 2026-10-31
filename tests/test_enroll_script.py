import argparse
import asyncio
import importlib.util
import sys
import time
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/enroll_accounts.py"
spec = importlib.util.spec_from_file_location("enroll_accounts", SCRIPT)
enroll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enroll)


def test_targets_map_plus_numbers_to_aliases_and_profiles() -> None:
    args = enroll.parser().parse_args(
        ["--email", "me@gmail.com", "--first", "27", "--last", "29", "--first-alias", "53"]
    )
    assert enroll.targets(args) == [
        enroll.Target(27, "ansuman-53", "me+27@gmail.com", "Plus 27"),
        enroll.Target(28, "ansuman-54", "me+28@gmail.com", "Plus 28"),
        enroll.Target(29, "ansuman-55", "me+29@gmail.com", "Plus 29"),
    ]


@pytest.mark.parametrize("base", ["me+1@gmail.com", "gmail.com", "@gmail.com"])
def test_plus_address_requires_a_plain_base(base: str) -> None:
    with pytest.raises(enroll.EnrollError):
        enroll.plus_address(base, 1)


def test_reversed_range_is_rejected() -> None:
    args = argparse.Namespace(
        email="me@gmail.com", first=5, last=4, first_alias=1, prefix="a", profile_prefix="P"
    )
    with pytest.raises(enroll.EnrollError):
        enroll.targets(args)


def test_login_url_survives_wrapping_and_styling() -> None:
    output = (
        b"Visit \x1b[1mhttps://app.devin.ai/auth/cli/continue?state=abc&prompt=select_\r\n"
        b"account&code_challenge=xyz&cli_pkce_marker=1\x1b[0m to sign in\r\nCode:"
    )
    assert enroll.login_url(output) == (
        "https://app.devin.ai/auth/cli/continue?state=abc&prompt=select_account"
        "&code_challenge=xyz&cli_pkce_marker=1"
    )
    assert enroll.login_url(b"Opening Plus 1. Code:") is None


def test_other_sign_in_link_is_recognized_but_not_used() -> None:
    other = (
        b"Visit \x1b]8;;https://windsurf.com/account/login?redirect_uri=x&state=abc"
        b"&response_type=code&code_challenge=xyz&cli_pkce_marker=1\x1b\\link\x1b]8;;\x1b\\"
        b" to sign in\r\nCode:"
    )
    assert enroll.login_url(other) is None
    assert enroll.other_login_url(other)
    devin = b"Visit https://app.devin.ai/auth/cli/continue?state=abc&cli_pkce_marker=1 Code:"
    assert enroll.login_url(devin)
    assert not enroll.other_login_url(devin)
    assert not enroll.other_login_url(b"Opening Plus 1. Code:")


def test_submit_keeps_reading_until_login_exits(tmp_path: Path) -> None:
    fake = tmp_path / "ds"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "sys.stdin.readline()\n"
        "for _ in range(400):\n"
        "    print('verifying ' * 40, flush=True)\n"
        "time.sleep(1)\n"
        "print('Login saved for fake.', flush=True)\n"
    )
    fake.chmod(0o755)
    login = enroll.Login(str(fake), "fake")
    start = time.monotonic()
    try:
        assert asyncio.run(login.submit("code")) == "Login saved for fake."
        assert time.monotonic() - start < 15
    finally:
        login.close()


def test_redact_hides_code_like_values() -> None:
    text = "Copy your login code\nabcdefghijklmnopqrstuvwxyz0123\nCopy code"
    assert "abcdefghij" not in enroll.redact(text)
    assert "Copy your login code" in enroll.redact(text)


@pytest.fixture
def fake_playwright(monkeypatch: pytest.MonkeyPatch):
    browser = FakeBrowser()

    @asynccontextmanager
    async def async_playwright():
        yield types.SimpleNamespace(chromium=types.SimpleNamespace(connect_over_cdp=connect))

    async def connect(url: str, timeout: int) -> "FakeBrowser":
        return browser

    module = types.ModuleType("playwright.async_api")
    module.async_playwright = async_playwright
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    return browser


class FakePage:
    def __init__(self, url: str = "about:blank", title: str = "") -> None:
        self.url, self._title, self.opened = url, title, None

    async def title(self) -> str:
        return self._title

    async def goto(self, url: str, timeout: int) -> None:
        self.opened = url
        self.url, self._title = FakeBrowser.landing

    async def wait_for_timeout(self, ms: int) -> None:
        pass


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    landing = ("https://mail.google.com/mail/u/0/#inbox", "Inbox - me@gmail.com - Gmail")

    def __init__(self) -> None:
        self.contexts = [FakeContext([FakePage("https://example.com", "Example")])]
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected


def plan_args(*extra: str) -> argparse.Namespace:
    return enroll.parser().parse_args(
        ["--email", "me@gmail.com", "--first", "1", "--last", "2", "--first-alias", "1"]
        + ["--pause", "0"]
        + ["--pass-wait", "0", *extra]
    )


def fake_enroller(monkeypatch, failures: dict[str, int], browser: FakeBrowser) -> list[str]:
    calls: list[str] = []

    class Enroller:
        def __init__(self, args: argparse.Namespace) -> None:
            self.browser = browser

        async def connect(self, playwright) -> None:
            pass

        async def enroll(self, target) -> str:
            calls.append(target.alias)
            if failures.get(target.alias, 0) > 0:
                failures[target.alias] -= 1
                raise enroll.EnrollError("rate limited")
            return "Teams"

    monkeypatch.setattr(enroll, "Enroller", Enroller)
    monkeypatch.setattr(enroll, "signed_in", lambda alias: False)
    return calls


def test_failed_seat_is_retried_on_the_next_pass(monkeypatch, fake_playwright, capsys) -> None:
    args = plan_args("--passes", "2")
    calls = fake_enroller(monkeypatch, {"ansuman-2": 1}, fake_playwright)
    assert asyncio.run(enroll.run(args, enroll.targets(args))) == 0
    assert calls == ["ansuman-1", "ansuman-2", "ansuman-2"]
    output = capsys.readouterr().out
    assert "pass 1 left 1: ansuman-2" in output
    assert "ALL DONE" in output


def test_run_gives_up_after_the_last_pass(monkeypatch, fake_playwright, capsys) -> None:
    args = plan_args("--passes", "2")
    calls = fake_enroller(monkeypatch, {"ansuman-1": 5}, fake_playwright)
    assert asyncio.run(enroll.run(args, enroll.targets(args))) == 1
    assert calls == ["ansuman-1", "ansuman-2", "ansuman-1"]
    assert "GAVE UP on: ansuman-1" in capsys.readouterr().out


def test_lost_browser_connection_stops_the_run(monkeypatch, fake_playwright) -> None:
    args = plan_args("--passes", "3")
    calls = fake_enroller(monkeypatch, {"ansuman-1": 1}, fake_playwright)
    fake_playwright.connected = False
    with pytest.raises(enroll.EnrollError):
        asyncio.run(enroll.run(args, enroll.targets(args)))
    assert calls == ["ansuman-1"]


@pytest.fixture
def chrome_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/x\n")
    monkeypatch.setattr(enroll, "CHROME_DATA", tmp_path)


def connect(browser: FakeBrowser) -> "enroll.Enroller":
    enroller = enroll.Enroller(plan_args())

    async def go() -> None:
        connect = types.SimpleNamespace(connect_over_cdp=lambda url, timeout: _ready(browser))
        await enroller.connect(types.SimpleNamespace(chromium=connect))

    asyncio.run(go())
    return enroller


async def _ready(value):
    return value


def test_connect_opens_gmail_for_the_account_when_no_tab_matches(chrome_port) -> None:
    browser = FakeBrowser()
    enroller = connect(browser)
    assert enroller.gmail.opened == "https://mail.google.com/mail/?authuser=me@gmail.com"
    assert enroller.gmail in browser.contexts[0].pages


def test_connect_reuses_an_open_gmail_tab_in_any_context(chrome_port) -> None:
    browser = FakeBrowser()
    tab = FakePage(*FakeBrowser.landing)
    browser.contexts.append(FakeContext([tab]))
    assert connect(browser).gmail is tab
    assert len(browser.contexts[0].pages) == 1


def test_connect_fails_when_gmail_opens_for_another_account(chrome_port, monkeypatch) -> None:
    monkeypatch.setattr(FakeBrowser, "landing", ("https://mail.google.com/", "other@gmail.com"))
    with pytest.raises(enroll.EnrollError):
        connect(FakeBrowser())


def test_blocked_chrome_port_is_read_through_the_popup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chrome_helper, protect
) -> None:
    from devin_switch import chrome_access

    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/x\n")
    monkeypatch.setattr(enroll, "CHROME_DATA", tmp_path)
    monkeypatch.setattr(chrome_access, "HELPER_DATA", tmp_path)
    chrome_helper.protection = protect(tmp_path)
    seen = []

    async def go() -> None:
        def cdp(url: str, timeout: int):
            seen.append(url)
            return _ready(FakeBrowser())

        await enroll.Enroller(plan_args()).connect(
            types.SimpleNamespace(chromium=types.SimpleNamespace(connect_over_cdp=cdp))
        )

    asyncio.run(go())
    assert seen == ["ws://127.0.0.1:9222/devtools/browser/x"]
    assert [request["items"] for request in chrome_helper.requests] == [["DevToolsActivePort"]]


def test_profile_check_sees_new_folders_when_blocked_and_waits_for_an_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chrome_helper, protect
) -> None:
    from devin_switch import browser

    monkeypatch.setattr(browser, "CHROME_DATA", tmp_path)
    (tmp_path / "Plus 1").mkdir()
    assert not enroll.has_profile("Plus 1")
    (tmp_path / "Local State").write_text('{"profile": {"info_cache": {}}}')
    assert not enroll.has_profile("Plus 1")
    protect(tmp_path)
    assert enroll.has_profile("Plus 1") and not enroll.has_profile("Plus 2")
    assert chrome_helper.requests == []


def test_chrome_data_follows_ds_chrome_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DS_CHROME_DATA", str(tmp_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CHROME_DATA == tmp_path


class FakeAction:
    def __init__(self, page: "FakeSignupPage", kind: str) -> None:
        self.page, self.kind = page, kind

    def or_(self, other: "FakeAction") -> "FakeAction":
        return self

    @property
    def first(self) -> "FakeAction":
        return self

    async def count(self) -> int:
        if self.kind == "checkbox":
            return 2 if self.page.checkbox_present else 0
        return 1

    async def click(self) -> None:
        if self.kind == "link":
            self.page.signup_mode = True
        elif self.kind == "checkbox":
            if self.page.span_click_agrees:
                self.page.agreed = True
        elif self.kind == "button":
            self.page.submits += 1

    async def fill(self, value: str) -> None:
        self.page.email = value

    async def check(self, force: bool = False, timeout: int = 0) -> None:
        self.page.agreed = True
        self.page.force_checked = force


class FakeSignupPage:
    def __init__(self, checkbox_present: bool = True, span_click_agrees: bool = True) -> None:
        self.url = "https://app.devin.ai/auth/login"
        self.checkbox_present = checkbox_present
        self.span_click_agrees = span_click_agrees
        self.signup_mode = False
        self.agreed = False
        self.force_checked = False
        self.submits = 0
        self.email = ""

    async def goto(self, url: str, **kwargs: object) -> None:
        self.url = url

    async def wait_for_timeout(self, ms: int) -> None:
        return None

    async def inner_text(self, selector: str) -> str:
        if self.signup_mode and self.checkbox_present and not self.agreed:
            return "Please agree before signing up."
        return "Welcome We've sent you a code"

    def get_by_label(self, label: str) -> FakeAction:
        return FakeAction(self, "fill")

    def get_by_role(self, role: str, name: str = "", exact: bool = False) -> FakeAction:
        return FakeAction(self, role)

    def locator(self, selector: str) -> FakeAction:
        return FakeAction(self, "checkbox-input")


def request_code(page: FakeSignupPage, signup: bool) -> bool:
    enroller = enroll.Enroller(argparse.Namespace(plan="Teams"))
    return asyncio.run(
        enroller.request_code(page, "https://app.devin.ai/auth/login", "me+1@gmail.com", signup)
    )


def test_signup_agrees_to_terms_via_checkbox() -> None:
    page = FakeSignupPage(span_click_agrees=True)
    assert request_code(page, signup=True)
    assert page.submits == 1
    assert not page.force_checked


def test_signup_force_checks_hidden_input_when_checkbox_click_is_ignored() -> None:
    page = FakeSignupPage(span_click_agrees=False)
    assert request_code(page, signup=True)
    assert page.submits == 2
    assert page.force_checked


def test_signup_without_checkbox_still_submits() -> None:
    page = FakeSignupPage(checkbox_present=False)
    assert request_code(page, signup=True)
    assert page.submits == 1


def test_login_needs_no_agreement() -> None:
    page = FakeSignupPage(span_click_agrees=False)
    assert request_code(page, signup=False)
    assert page.submits == 1
