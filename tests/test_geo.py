"""Wi-Fi access point lookups: the request, the answer, what is never sent, and the log."""

import json
import struct

import pytest

from connected_phone_forensics import cli, geo


def _answer(mac: str, lat: int, lon: int, acc: int) -> bytes:
    loc = geo._field(1, 0, lat) + geo._field(2, 0, lon) + geo._field(3, 0, acc)
    wifi = geo._field(1, 2, mac.encode()) + geo._field(2, 2, loc)
    return b"\x00" * 10 + geo._field(2, 2, wifi)


def test_addresses_are_normalised_and_random_ones_recognised():
    assert geo.normalise("3C-22-FB-9A-10-4E") == "3c:22:fb:9a:10:4e"
    assert not geo.is_randomised("3c:22:fb:9a:10:4e")
    assert all(geo.is_randomised(m) for m in ("aa:e3:81:0c:fc:a2", "06:df:76:31:61:83", "de:f3:e3:8d:03:80"))
    with pytest.raises(ValueError):
        geo.normalise("not a mac")


def test_request_carries_only_the_address():
    body = geo.request_body("3C:22:FB:9A:10:4E")
    assert body.startswith(geo._PREAMBLE)
    (length,) = struct.unpack(">H", body[len(geo._PREAMBLE):len(geo._PREAMBLE) + 2])
    message = body[len(geo._PREAMBLE) + 2:]
    assert len(message) == length and b"3c:22:fb:9a:10:4e" in message


def test_answers_are_read_with_apples_short_mac_form():
    found = geo.parse_response(_answer("3c:22:fb:9a:10:4e", 1297654321, 7759876543, 32), "3c:22:fb:9a:10:4e")
    assert found.found and found.accuracy_m == 32
    assert round(found.latitude, 6) == 12.976543 and round(found.longitude, 6) == 77.598765
    assert "openstreetmap.org" in found.map_url
    short = geo.parse_response(_answer("3c:22:fb:9a:10:4e".replace(":0", ":"), -337000000, 1512000000, 50),
                               "3c:22:fb:9a:10:4e")
    assert short.found and short.latitude < 0  # southern hemisphere: a negative varint
    missing = geo.parse_response(_answer("3c:22:fb:9a:10:4e", geo._NOT_FOUND, geo._NOT_FOUND, 0),
                                 "3c:22:fb:9a:10:4e")
    assert not missing.found and missing.map_url is None


def test_random_addresses_are_never_sent(monkeypatch):
    def no_network(*_a, **_k):
        raise AssertionError("a randomised address must not be sent")

    monkeypatch.setattr(geo.urllib.request, "urlopen", no_network)
    loc = geo.locate("aa:e3:81:0c:fc:a2")
    assert not loc.found and "not sent" in loc.note


def test_cli_locate_logs_every_lookup(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_locate(mac, **_k):
        calls.append(mac)
        return geo.Location(mac, True, 12.9716, 77.5946, 40)

    monkeypatch.setattr(geo, "locate", fake_locate)
    code = cli.main(["locate", "3C:22:FB:9A:10:4E", "aa:e3:81:0c:fc:a2", "--yes", "--case-dir", str(tmp_path),
                     "--examiner", "AB"])
    assert code == 0 and calls == ["3c:22:fb:9a:10:4e", "aa:e3:81:0c:fc:a2"]
    log = [json.loads(line) for line in (tmp_path / "online-lookups.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["sent"] for e in log] == [True, False] and log[0]["examiner"] == "AB"
    assert "12.971600, 77.594600" in capsys.readouterr().out


def test_cli_locate_asks_before_sending(monkeypatch, capsys):
    monkeypatch.setattr(geo, "locate", lambda *_a, **_k: pytest.fail("sent without asking"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["locate", "3c:22:fb:9a:10:4e"]) == 2 and "--yes" in capsys.readouterr().err
