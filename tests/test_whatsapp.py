"""WhatsApp group front door with the bridge, model and network stubbed: tracking, automatic requirement detection, admin decisions, replies."""
import json
import pytest
from app import db, engine, whatsapp as wa


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db")); db.init(reset=True)
    monkeypatch.setattr(engine.mailer, "send", lambda *a, **k: "stub")
    monkeypatch.setattr(wa, "ask_json", lambda *a, **k: {"is_requirement": True, "summary": "cement for Demo Greens"})
    wa.ADMINS.clear()
    parsed = {"item_description": "toilet floor waterproofing", "category": "waterproofing", "project": "Aquina", "location": None, "quantity": 500, "uom": "sqft", "needed_by": "10 Dec", "missing": []}
    monkeypatch.setattr(engine, "parse", lambda t: dict(parsed))


def msg(i, text, sender="9111@s.whatsapp.net", **kw):
    return {"msg_id": f"m{i}", "jid": "grp@g.us", "is_group": True, "group_name": "Site Team", "sender": sender, "sender_name": "Ravi", "text": text,
            "mentioned": [], "my_ids": ["9000:1@s.whatsapp.net"], "admins": ["9222@s.whatsapp.net"], **kw}


def rows(sql, *a):
    c = db.con(); r = [dict(x) for x in c.execute(sql, a)]; c.close(); return r


def test_every_message_is_tracked_and_chatter_is_ignored(monkeypatch):
    monkeypatch.setattr(wa, "ask_json", lambda *a, **k: {"is_requirement": False, "summary": ""})
    assert wa.inbound(msg(1, "good morning all"))["handled"] == "tracked"
    assert wa.inbound(msg(2, "need 200 bags cement at Demo Greens by 5 Dec"))["handled"] == "tracked"       # looked like one, the model said no
    assert len(rows("select * from wa_messages")) == 2 and not rows("select * from cases")
    assert wa.inbound(msg(1, "good morning all"))["dup"]                                                       # no double handling


def test_agent_notices_a_requirement_without_being_asked_and_replies_in_the_group():
    r = wa.inbound(msg(3, "need 500 sqft toilet floor waterproofing at Aquina by 10 Dec"))
    assert r["handled"] == "noticed a requirement"
    c = rows("select * from cases")[0]; assert c["state"] != "NEW" and "whatsapp" in json.loads(c["data"])["channel"]
    out = wa.outbox(); assert out and all(o["jid"] == "grp@g.us" for o in out)
    assert any(o["mentions"] == ["9222@s.whatsapp.net"] and "@9222" in o["text"] for o in out)                   # the admins are tagged, in the group
    wa.ack([o["id"] for o in out]); assert wa.outbox() == []


def test_only_group_admins_decide_and_a_number_is_enough(monkeypatch):
    monkeypatch.setattr(engine, "bg", lambda f, *a: f(*a))
    wa.inbound(msg(4, "need 500 sqft toilet floor waterproofing at Aquina by 10 Dec")); wa.outbox()
    cid = rows("select id from cases")[0]["id"]; st = engine._case(cid)["state"]
    assert engine._case(cid)["waiting"] == "hod", st                                  # item code has variants: the admins decide
    wa.outbox()
    r = wa.inbound(msg(5, "1", sender="9111@s.whatsapp.net")); assert r["handled"] != "hod decision"       # the requester is not an admin
    r = wa.inbound(msg(6, "1", sender="9222@s.whatsapp.net")); assert r["handled"] == "hod decision"        # an admin is
    assert engine._case(cid)["state"] in ("STOCK", "RFQ", "QUOTING")


def test_replies_are_localised_for_a_voice_sender_and_status_works(monkeypatch):
    from app import call
    monkeypatch.setattr(call, "localize", lambda t, l: f"[{l}] {t}")
    monkeypatch.setattr(wa.voice, "stt", lambda b, n: ("need 500 sqft toilet floor waterproofing at Aquina by 10 Dec", "hi-IN"))
    monkeypatch.setattr(wa.voice, "tts", lambda t, l: b"wav"); monkeypatch.setattr(wa.voice, "to_ogg_opus", lambda w: b"ogg")
    wa.inbound(msg(7, "", audio_b64="AAAA"))
    out = wa.outbox(); assert any(o["text"].find("[hi-IN]") >= 0 for o in out) and any(o["ogg_b64"] for o in out)   # Hindi text and a voice note back
    wa.inbound(msg(8, "agent status", mentioned=[])); assert any("Cases from this group" in o["text"] for o in wa.outbox())
