"""Full agent flow with the LLM and SMTP stubbed: vague message -> clarify -> BOQ match -> RFQs -> quotes -> deadline -> HOD -> closed."""
from pathlib import Path
from app import db, engine, mailer
from app.tender import DATA_DIR


def test_case_runs_without_clicks(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db")); db.init(reset=True)
    monkeypatch.setattr(engine, "parse", lambda t: {"item_description": "toilet waterproofing", "category": "waterproofing", "project": None, "location": None, "quantity": None, "uom": None, "needed_by": None, "missing": []})
    monkeypatch.setattr(engine, "merge", lambda p, r: {**p, "project": "Demo Heights", "needed_by": "10 Dec"})
    monkeypatch.setattr(engine.recommend, "summary", lambda rows: ("stub summary", "stub"))
    sent = []
    monkeypatch.setattr(mailer, "send", lambda kind, to, s, b: sent.append((kind, to)) or "stub")
    cid = engine.new_case("waterproofing for the toilets, urgent"); engine.advance(cid)
    assert engine._case(cid)["waiting"] == "engineer"                      # agent asked, did not guess
    engine.on_engineer_message(cid, "Demo Heights, needed by 10 Dec")
    assert engine._case(cid)["state"] == "QUOTING" and len([s for s in sent if s[0] == "rfq"]) >= 3
    assert all(to.endswith("@rfq-test.example") for k, to in sent if k == "rfq")
    for f in sorted((DATA_DIR / "quotes").glob("*.pdf")): engine.on_vendor_reply(cid, f)
    assert engine._case(cid)["state"] == "QUOTING"                         # two vendors silent: waits for the deadline
    assert any(k == "followup" for k, _ in sent)                          # incomplete quotes get questions, nobody clicked
    engine.advance_time(cid, 24)
    assert len([s for s in sent if s[0] == "chaser"]) == 2                 # the two silent vendors are chased once
    assert engine._case(cid)["state"] == "QUOTING"
    engine.advance_time(cid, 24)
    c = engine._case(cid); assert c["state"] == "AWAITING_HOD" and c["waiting"] == "hod"
    assert len(db.signals(engine._case(cid)["data"]["recommended_key"])) >= 1   # behaviour of vendors was recorded
    engine.on_hod_action(cid, "decision:approve")
    assert engine._case(cid)["state"] == "IN_WORK"
    engine.advance_time(cid, 24 * 61)
    assert engine._case(cid)["state"] == "CHECKIN" and engine._case(cid)["waiting"] == "engineer"
    key = engine._case(cid)["data"]["recommended_key"]; before = len(db.history(key))
    for a in ("checkin:t:4", "checkin:q:4", "checkin:i:dispute"): engine.on_hod_action(cid, a)
    assert engine._case(cid)["state"] == "DECIDED"
    last = db.history(key)[-1]; assert (last["quality"], last["delay_days"], last["disputes"], last["on_time"]) == (4, 4, 1, 0) and len(db.history(key)) == before + 1
