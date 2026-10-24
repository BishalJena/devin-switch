import gzip
import http.client
import json
import threading
import time
from urllib.parse import urlsplit

import pytest
from fake_upstream import INFERENCE, QUOTA, Upstream, api_key, frame, reply

from devin_switch import relay
from devin_switch.store import Account

NAMES = ("ansuman-1", "ansuman-2", "ansuman-3", "ansuman-4", "ansuman-5")


@pytest.fixture
def upstream():
    result = Upstream(NAMES)
    yield result
    result.close()


def make_login(upstream, name, *, key=None, server=None):
    return relay.Login(Account(name, None), key or api_key(name), server or upstream.url)


class Rotations:
    def __init__(self, upstream, order, *, delay=0.0):
        self.upstream = upstream
        self.order = list(order)
        self.delay = delay
        self.calls = []

    def __call__(self, current, tried):
        self.calls.append((current.account.name, tried))
        time.sleep(self.delay)
        name = next((name for name in self.order if name not in tried), None)
        return make_login(self.upstream, name) if name else None


@pytest.fixture
def started():
    relays = []

    def start(issued, rotate):
        result = relay.Relay(issued, rotate)
        relays.append(result)
        return result

    yield start
    for result in relays:
        result.close()


def post(proxy, path=INFERENCE, *, key=None, body=None, headers=()):
    key = key or proxy.issued.key
    connection = http.client.HTTPConnection("127.0.0.1", urlsplit(proxy.url).port, timeout=10)
    connection.request(
        "POST",
        path,
        body=body if body is not None else b"\x0a\x30" + key.encode() + b"\x12\x02hi",
        headers={
            "Authorization": f"Basic {key}-sk",
            "Content-Type": "application/proto",
            **dict(headers),
        },
    )
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def test_quota_stream_is_replayed_with_the_next_login_and_later_requests_stay_there(
    upstream, started
):
    upstream.exhausted = {"ansuman-1"}
    rotations = Rotations(upstream, ["ansuman-2"])
    proxy = started(make_login(upstream, "ansuman-1"), rotations)

    assert post(proxy) == (200, reply("ansuman-2"))
    assert upstream.inference() == [("ansuman-1", "ansuman-1"), ("ansuman-2", "ansuman-2")]
    replayed = upstream.requests[-1]["raw"]
    assert replayed == b"\x0a\x30" + api_key("ansuman-2").encode() + b"\x12\x02hi"
    assert rotations.calls == [("ansuman-1", frozenset({"ansuman-1"}))]

    assert post(proxy, "/exa.seat_management_pb.SeatManagementService/GetUserStatus") == (
        200,
        b"status ansuman-2",
    )
    assert post(proxy) == (200, reply("ansuman-2"))
    assert upstream.inference()[2:] == [("ansuman-2", "ansuman-2")]
    assert len(rotations.calls) == 1
    assert proxy.active.account.name == "ansuman-2"


def test_quota_frame_reaches_the_native_unchanged_when_no_login_is_available(upstream, started):
    upstream.exhausted = {"ansuman-1"}
    rotations = Rotations(upstream, [])
    proxy = started(make_login(upstream, "ansuman-1"), rotations)

    assert post(proxy) == (200, frame(2, json.dumps(QUOTA).encode()))
    assert upstream.inference() == [("ansuman-1", "ansuman-1")]
    assert len(rotations.calls) == 1
    assert proxy.active.account.name == "ansuman-1"


@pytest.mark.parametrize(
    "first",
    [
        frame(2, json.dumps({"error": {"code": "unavailable", "message": "overloaded"}}).encode()),
        frame(
            2, json.dumps({"error": {"code": "failed_precondition", "message": "nope"}}).encode()
        ),
        frame(0, json.dumps(QUOTA).encode()) + frame(2, b"{}"),
        frame(2, b"not json"),
        frame(2, b"[]"),
        frame(3, json.dumps(QUOTA).encode()),
    ],
    ids=["other-error", "other-precondition", "data-frame", "garbage", "array", "bad-gzip"],
)
def test_other_streams_pass_through_without_rotating(upstream, started, first):
    upstream.first_frame = first
    rotations = Rotations(upstream, ["ansuman-2"])
    proxy = started(make_login(upstream, "ansuman-1"), rotations)

    assert post(proxy) == (200, first)
    assert upstream.inference() == [("ansuman-1", "ansuman-1")]
    assert not rotations.calls


def test_gzip_compressed_quota_frame_is_detected(upstream, started):
    compressed = frame(3, gzip.compress(json.dumps(QUOTA).encode()))
    assert relay.quota_frame(compressed, "gzip")
    assert not relay.quota_frame(compressed, None)
    weekly = json.dumps(
        {"error": {"message": "Your weekly usage quota has been exhausted. Visit …"}}
    ).encode()
    assert relay.quota_frame(frame(2, weekly), None)
    assert not relay.quota_frame(frame(2, weekly)[:-1], None)


def test_requests_without_the_issued_key_are_refused(upstream, started):
    proxy = started(make_login(upstream, "ansuman-1"), Rotations(upstream, ["ansuman-2"]))

    assert post(proxy, key=api_key("ansuman-2")) == (403, b"")
    assert not upstream.requests


def test_requests_carrying_the_issued_key_only_in_the_body_are_forwarded(upstream, started):
    proxy = started(make_login(upstream, "ansuman-1"), Rotations(upstream, []))
    port = urlsplit(proxy.url).port
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    analytics = "/exa.product_analytics_pb.ProductAnalyticsService/BatchRecordAnalyticsEvents"
    connection.request("POST", analytics, body=b"events:" + proxy.issued.key.encode())
    assert connection.getresponse().read() == b"status None"
    assert upstream.requests[-1]["body"] == "ansuman-1"


@pytest.mark.parametrize("mismatch", ["key-length", "server"])
def test_incompatible_replacement_logins_are_not_used(upstream, started, mismatch):
    upstream.exhausted = {"ansuman-1"}
    proxy = started(
        make_login(upstream, "ansuman-1"),
        lambda current, tried: make_login(
            upstream,
            "ansuman-2",
            key=api_key("ansuman-2") + "y" if mismatch == "key-length" else None,
            server="https://other.invalid" if mismatch == "server" else None,
        ),
    )

    assert post(proxy) == (200, frame(2, json.dumps(QUOTA).encode()))
    assert upstream.inference() == [("ansuman-1", "ansuman-1")]
    assert proxy.active.account.name == "ansuman-1"


def test_concurrent_quota_errors_rotate_once(upstream, started):
    upstream.exhausted = {"ansuman-1"}
    rotations = Rotations(upstream, ["ansuman-2", "ansuman-3"], delay=0.3)
    proxy = started(make_login(upstream, "ansuman-1"), rotations)
    results = []
    threads = [threading.Thread(target=lambda: results.append(post(proxy))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [(200, reply("ansuman-2"))] * 4
    assert len(rotations.calls) == 1


def test_rotation_stops_after_the_limit(upstream, started):
    upstream.exhausted = set(NAMES)
    rotations = Rotations(upstream, NAMES[1:])
    proxy = started(make_login(upstream, "ansuman-1"), rotations)

    assert post(proxy) == (200, frame(2, json.dumps(QUOTA).encode()))
    assert len(rotations.calls) == relay.ROTATION_LIMIT
    assert [header for header, _ in upstream.inference()] == list(NAMES[: relay.ROTATION_LIMIT + 1])


def test_chunked_request_bodies_are_forwarded_intact(upstream, started):
    proxy = started(make_login(upstream, "ansuman-1"), Rotations(upstream, []))
    port = urlsplit(proxy.url).port
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = b"payload-" + proxy.issued.key.encode()
    connection.request(
        "POST",
        INFERENCE,
        body=iter([body[:5], body[5:]]),
        headers={"Authorization": proxy.issued.key, "Transfer-Encoding": "chunked"},
        encode_chunked=True,
    )
    assert connection.getresponse().read() == reply("ansuman-1")
    assert upstream.requests[-1]["raw"] == body


def test_unreachable_upstream_is_reported_as_bad_gateway(upstream, started):
    proxy = started(
        make_login(upstream, "ansuman-1", server="http://127.0.0.1:9"), Rotations(upstream, [])
    )
    assert post(proxy)[0] == 502


def test_saved_login_requires_a_regular_toml_api_key(store, tmp_path):
    account = store.account("ansuman-1")
    path = store.credentials(account)
    cases = {
        'windsurf_api_key = "k"\napi_server_url = "https://server.example/"\n': relay.Login(
            account, "k", "https://server.example"
        ),
        "valid-ansuman-1": None,
        'api_server_url = "https://server.example"\n': None,
        'windsurf_api_key = "k"\napi_server_url = "ftp://server.example"\n': None,
        'windsurf_api_key = ""\napi_server_url = "https://server.example"\n': None,
    }
    for content, expected in cases.items():
        path.write_text(content)
        assert relay.login(store, account) == expected
    target = tmp_path / "elsewhere.toml"
    target.write_text('windsurf_api_key = "k"\napi_server_url = "https://server.example"\n')
    path.unlink()
    path.symlink_to(target)
    assert relay.login(store, account) is None
    path.unlink()
    assert relay.login(store, account) is None
