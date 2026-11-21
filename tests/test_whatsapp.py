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
    out = wa.outbox(); assert out and any(o["jid"] == "grp@g.us" for o in out) and all(o["jid"] in ("grp@g.us", "9222@s.whatsapp.net") for o in out)
    assert any(o["jid"] == "9222@s.whatsapp.net" and "[case #1]" in o["text"] for o in out)   # the admin gets the recommendation privately
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


def test_a_hindi_message_is_judged_by_the_model_not_dropped_by_the_english_prefilter():
    r = wa.inbound(msg(20, "डेमो विला के लिए दो सौ बैग सीमेंट चाहिए 10 दिसंबर तक"))
    assert r["handled"] == "noticed a requirement"


def test_each_reply_is_served_once_even_if_the_bridge_polls_again_before_acking():
    wa.inbound(msg(30, "need 500 sqft toilet floor waterproofing at Aquina by 10 Dec")); first = wa.outbox()
    assert first and wa.outbox() == []                        # a second poll before the ack must not return the same messages
    c = db.con(); c.execute("update wa_out set claimed_at=datetime('now','-120 seconds') where sent=2"); c.commit(); c.close()
    again = wa.outbox(); assert [o["id"] for o in again] == [o["id"] for o in first]      # a claim that was never acked is retried after 90 s
    wa.ack([o["id"] for o in again]); assert wa.outbox() == []


def test_an_older_database_gets_the_new_columns(tmp_path, monkeypatch):
    import sqlite3
    f = tmp_path / "old.db"; c = sqlite3.connect(f); c.execute("create table wa_out(id integer primary key autoincrement, jid text, text text, mentions text, ogg_b64 text, sent integer default 0, ts text)"); c.commit(); c.close()
    monkeypatch.setattr(db, "DB_PATH", str(f)); db.init()
    cols = {r[1] for r in sqlite3.connect(f).execute("pragma table_info(wa_out)")}; assert "claimed_at" in cols


def test_a_news_forward_or_greeting_addressed_to_the_agent_gets_a_polite_reply_not_a_case(monkeypatch):
    monkeypatch.setattr(wa, "ask_json", lambda *a, **k: {"is_requirement": False, "summary": ""})
    r = wa.inbound({**msg(40, "JUST IN: Indian stock markets surged to nine-month highs today"), "is_group": False})        # a direct message counts as addressed
    assert r["handled"] == "not a requirement" and not rows("select * from cases")
    assert any("I handle procurement" in o["text"] for o in wa.outbox())


def test_admin_decides_by_private_message():
    wa.inbound(msg(30, "need 200 bags cement at Demo Villas by 15 Dec"))
    wa.outbox()
    cid = rows("select case_id from wa_cases")[0]["case_id"]
    assert rows("select waiting from cases where id=?", cid)[0]["waiting"] == "hod"
    r = wa.inbound(msg(31, "1", sender="9222@s.whatsapp.net", is_group=False, jid="9222@s.whatsapp.net"))
    assert r["handled"] == "hod decision (private)"
