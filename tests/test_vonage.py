"""Vonage adapter with the network stubbed: the call is created with the right NCCO, only allowed numbers are dialled, and each recorded turn transfers the live call."""
import json, threading
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
import httpx
from app import main


@pytest.fixture
def setup(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    f = tmp_path / "k.key"; f.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    monkeypatch.setenv("VONAGE_APPLICATION_ID", "app-123"); monkeypatch.setenv("VONAGE_PRIVATE_KEY_PATH", str(f))
    monkeypatch.setenv("VONAGE_ALLOWED_TO", "+911234567890"); monkeypatch.setenv("PUBLIC_URL", "https://x.test")
    calls = {"post": [], "put": [], "get": []}
    class R:
        def __init__(s, code=200, js=None, content=b""): s.status_code = code; s._j = js; s.content = content; s.headers = {"content-type": "application/json"}; s.text = ""
        def json(s): return s._j
    monkeypatch.setattr(httpx, "post", lambda url, **k: calls["post"].append((url, k)) or R(201, {"uuid": "leg-1", "status": "started"}))
    monkeypatch.setattr(httpx, "put", lambda url, **k: calls["put"].append((url, k)) or R(204, {}))
    monkeypatch.setattr(httpx, "get", lambda url, **k: calls["get"].append((url, k)) or R(200, content=b"x" * 1000))
    monkeypatch.setattr(main.voice, "tts", lambda t, l="en-IN": b"RIFFwav"); monkeypatch.setattr(main.voice, "to_phone_wav", lambda w: w)
    main.AUDIO.clear(); main.VON.clear()
    return TestClient(main.app), calls


def test_call_only_allowed_numbers_and_ncco_shape(setup):
    c, calls = setup
    assert c.post("/telephony/vonage/call", data={"to": "+919999999999"}).status_code == 403          # not allowed: nothing dialled
    assert not calls["post"]
    r = c.post("/telephony/vonage/call", data={"to": "+91 1234567890"}); assert r.status_code == 200
    url, k = calls["post"][0]; body = k["json"]
    assert url.endswith("/v1/calls") and body["to"][0]["number"] == "911234567890" and k["headers"]["Authorization"].startswith("Bearer ")
    acts = [a["action"] for a in body["ncco"]]; assert acts == ["stream", "record", "stream"]            # greeting, record, hold the line
    assert body["ncco"][1]["eventUrl"][0].startswith("https://x.test/telephony/vonage/recording?sid=") and body["ncco"][2]["loop"] == 0


def test_each_turn_transfers_the_live_call(setup, monkeypatch):
    c, calls = setup
    c.post("/telephony/vonage/call", data={"to": "+911234567890"}); sid = next(iter(main.VON))
    results = [{"wav": b"a", "done": False, "case_id": None}, {"wav": b"b", "done": True, "case_id": None}]
    monkeypatch.setattr(main.callmod, "turn", lambda s, audio, fn: results.pop(0))
    done = threading.Event(); orig = httpx.put
    monkeypatch.setattr(httpx, "put", lambda url, **k: (orig(url, **k), done.set())[0])
    c.post(f"/telephony/vonage/recording?sid={sid}", json={"recording_url": "https://api.nexmo.com/v1/files/abc"}); assert done.wait(5)
    url, k = calls["put"][0]; assert url.endswith("/v1/calls/leg-1") and k["json"]["action"] == "transfer"
    assert [a["action"] for a in k["json"]["destination"]["ncco"]] == ["stream", "record", "stream"]   # reply, then listen again
    done.clear(); c.post(f"/telephony/vonage/recording?sid={sid}", json={"recording_url": "https://api.nexmo.com/v1/files/def"}); assert done.wait(5)
    assert [a["action"] for a in calls["put"][1][1]["json"]["destination"]["ncco"]] == ["stream"]        # confirmed: reply, then end
    assert c.post("/telephony/vonage/recording?sid=zzz", json={}).status_code == 403
