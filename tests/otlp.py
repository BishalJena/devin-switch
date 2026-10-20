"""Encode OTLP/HTTP protobuf log exports the way the installed CLI sends them."""

import struct


def varint(value: int) -> bytes:
    value &= (1 << 64) - 1
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def field(number: int, payload: bytes) -> bytes:
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def any_value(value: object) -> bytes:
    if isinstance(value, bool):
        return varint(2 << 3) + varint(int(value))
    if isinstance(value, int):
        return varint(3 << 3) + varint(value)
    if isinstance(value, float):
        return varint(4 << 3 | 1) + struct.pack("<d", value)
    return field(1, str(value).encode())


def key_value(key: str, value: object) -> bytes:
    return field(1, key.encode()) + field(2, any_value(value))


def encode_logs(records: list[dict], resource: dict | None = None) -> bytes:
    resource_attributes = b"".join(
        field(1, key_value(key, value))
        for key, value in (resource or {"service.name": "devin-local"}).items()
    )
    log_records = b""
    for record in records:
        attributes = dict(record)
        name = attributes.get("event.name", "")
        encoded = varint(1 << 3 | 1) + struct.pack("<Q", 1)
        encoded += field(5, any_value(name))
        encoded += b"".join(field(6, key_value(key, value)) for key, value in attributes.items())
        log_records += field(2, encoded)
    scope = field(1, field(1, b"ai.devin.local.events")) + log_records
    return field(1, field(1, resource_attributes) + field(2, scope))
