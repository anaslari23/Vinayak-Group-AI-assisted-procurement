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
    engine.advance_time(cid, 48)
    c = engine._case(cid); assert c["state"] == "AWAITING_HOD" and c["waiting"] == "hod"
    engine.on_hod_action(cid, "decision:approve")
    assert engine._case(cid)["state"] == "DECIDED"
