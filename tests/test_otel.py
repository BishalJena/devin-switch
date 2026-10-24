import urllib.error
import urllib.request

import pytest
from otlp import encode_logs

from devin_switch import otel

QUOTA = {
    "event.name": "api_error",
    "error_kind": "ResourceExhausted",
    "error": "Your daily usage quota has been exhausted.",
    "model": "claude-opus-5-5-high",
    "session.id": "fine-rail",
    "duration_ms": 12,
}


def test_decodes_cli_log_export_with_resource_attributes():
    body = encode_logs(
        [{"event.name": "user_prompt", "prompt_length": 8}, QUOTA],
        {"service.name": "devin-local", "user.id": "u-1"},
    )
    first, second = otel.log_records(body)
    assert first == {
        "service.name": "devin-local",
        "user.id": "u-1",
        "event.name": "user_prompt",
        "prompt_length": 8,
    }
    assert second["event.name"] == "api_error" and second["duration_ms"] == 12
    assert second["session.id"] == "fine-rail"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"error_kind": "resource_exhausted"}, True),
        ({"retry_after_ms": 30000}, False),
        ({"error_kind": "Unavailable"}, False),
        ({"event.name": "tool_result"}, False),
    ],
)
def test_only_typed_quota_errors_without_retry_delay_count(changes, expected):
    (record,) = otel.log_records(encode_logs([{**QUOTA, **changes}]))
    assert otel.quota_exhausted(record) is expected


@pytest.mark.parametrize("body", [b"\x0a\xff", b"\x0b", b"\x0a\x05abc"])
def test_malformed_exports_are_rejected(body):
    with pytest.raises(ValueError):
        otel.log_records(body)


def post(url: str, body: bytes, headers: dict | None = None) -> int:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-protobuf", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_receiver_accepts_only_its_private_path():
    received = []
    with otel.Receiver(received.append) as receiver:
        assert receiver.endpoint.startswith("http://127.0.0.1:")
        wrong = receiver.endpoint.rsplit("/", 3)[0] + "/" + "0" * 32 + "/v1/logs"
        assert post(wrong, encode_logs([QUOTA])) == 404
        assert post(receiver.endpoint, encode_logs([QUOTA]), {"Content-Encoding": "gzip"}) == 400
        assert post(receiver.endpoint, b"\x0b") == 200
        assert received == []
        assert post(receiver.endpoint, encode_logs([QUOTA])) == 200
    assert [otel.quota_exhausted(record) for record in received] == [True]
