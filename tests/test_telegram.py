"""Telegram routing with the Telegram API and the agent stubbed: roles, buttons, delivery targets."""
from app import db, engine, telegram_bot as tb


def test_roles_buttons_and_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db")); db.init(reset=True)
    sent, calls = [], []
    monkeypatch.setattr(tb, "tg", lambda method, **p: calls.append((method, p)) or {"ok": True, "result": {}})
    monkeypatch.setattr(tb, "send", lambda chat, text, buttons=None, case_id=None: sent.append((chat, text, buttons)) or {"ok": True})
    monkeypatch.setattr(engine, "engineer_inbound", lambda text, channel, cid: 7)
    acted = []
    monkeypatch.setattr(engine, "on_hod_action", lambda cid, a: acted.append((cid, a)))
    msg = lambda chat, text: {"message": {"chat": {"id": chat}, "from": {"first_name": "A"}, "text": text}}
    tb.handle_update(msg(1, "waterproofing for toilets"))            # engineer by default
    assert tb._chat(1)["case_id"] == 7 and tb._chat(1)["role"] == "engineer"
    tb.handle_update(msg(2, "/hod")); assert tb._chat(2)["role"] == "hod"
    tb.handle_update(msg(2, "some text"));  assert "buttons" in sent[-1][1]  # HOD text gets a hint, not a new case
    tb.handle_update({"callback_query": {"id": "x", "data": "h|7|decision:approve"}}); assert acted == [(7, "decision:approve")]
    # delivery: HOD message goes to the HOD chat, engineer message to the chat that owns the case
    c = db.con(); c.execute("insert into cases(state,data) values('QUOTING','{\"channel\":\"telegram\"}')"); c.commit(); c.close()
    cid = 1; c = db.con(); c.execute("update chats set case_id=? where chat_id=1", (cid,)); c.commit(); c.close()
    engine.say(cid, "hod", "pick one", [{"label": "A", "data": "item:A"}]); engine.say(cid, "engineer", "hello")
    tb.deliver()
    assert {s[0] for s in sent[-2:]} == {1, 2}
