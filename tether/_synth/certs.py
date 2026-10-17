"""Build unsigned X.509-shaped DER certificates with chosen validity dates."""

from __future__ import annotations

import base64
from datetime import datetime


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _len(len(content)) + content


def _seq(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _name(cn: str) -> bytes:
    attr = _seq(_tlv(0x06, bytes.fromhex("550403")), _tlv(0x0C, cn.encode()))
    return _seq(_tlv(0x31, attr))


def make_cert_pem(not_before: datetime, not_after: datetime, cn: str = "Device", serial: int = 1) -> bytes:
    alg = _seq(_tlv(0x06, bytes.fromhex("2a864886f70d01010b")), _tlv(0x05, b""))
    validity = _seq(_tlv(0x17, not_before.strftime("%y%m%d%H%M%SZ").encode()),
                    _tlv(0x17, not_after.strftime("%y%m%d%H%M%SZ").encode()))
    tbs = _seq(
        _tlv(0xA0, _tlv(0x02, b"\x02")),
        _tlv(0x02, serial.to_bytes(4, "big")),
        alg, _name("Root"), validity, _name(cn), _seq(alg, _tlv(0x03, b"\x00")),
    )
    der = _seq(tbs, alg, _tlv(0x03, b"\x00" + b"\x01" * 16))
    b64 = base64.encodebytes(der).replace(b"\n", b"")
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return b"-----BEGIN CERTIFICATE-----\n" + b"\n".join(lines) + b"\n-----END CERTIFICATE-----\n"
