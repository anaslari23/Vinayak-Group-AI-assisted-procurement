"""Master workbook: loading, validation, safety of vendor addresses; stock answers; stock check before an RFQ."""
from pathlib import Path
import pytest
from openpyxl import load_workbook
from app import db, engine, master, stock
from app.tender import DATA_DIR


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db")); db.init(reset=True)


def test_workbook_loads_into_the_app():
    c = db.con(); n = {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in ("items", "materials", "stock", "usage", "vendors", "sites")}; c.close()
    assert all(v > 0 for v in n.values()), n


def test_bad_rows_are_reported_not_fatal(tmp_path):
    wb = load_workbook(DATA_DIR / "vinayak_master.xlsx")
    wb["Stock"].append(["Nowhere Site", "x", "MAT.NOPE", -5, "bag", 0, "2026-01-01", "t"])
    wb["Vendors"].append(["Real Vendor Pvt Ltd", "waterproofing", "yes", "owner@realvendor.com", ""])
    f = tmp_path / "bad.xlsx"; wb.save(f)
    r = master.sync(f); probs = " ".join(i["problem"] for i in r["issues"])
    assert "not in the Sites sheet" in probs and "not in the Materials sheet" in probs and "not a non-negative number" in probs and "not a test address" in probs
    c = db.con(); em = c.execute("select test_email from vendors where name='Real Vendor Pvt Ltd'").fetchone()["test_email"]; c.close()
    assert em.endswith("@rfq-test.example") and "realvendor" not in em          # a real address never gets in


def test_stock_question_answered_from_the_sheet():
    a = stock.answer("how much cement is left at Demo Heights?")
    assert a and "Demo Heights" in a and "340" in a and "Elsewhere" in a
    assert stock.answer("I need 200 bags of cement") is None                      # a purchase request is not a question
    assert stock.answer("how is the weather") is None


def test_specific_material_beats_generic():
    assert stock.find_material("tmt 16 mm bars")["code"] == "MAT.TMT.16"
    assert stock.find_material("steel")["code"] in ("MAT.TMT.12", "MAT.TMT.16")


def test_stock_checked_before_buying_and_transfer_recorded_not_executed(monkeypatch):
    monkeypatch.setattr(engine.mailer, "send", lambda *a, **k: "stub")
    parsed = {"item_description": "paver block 60 mm", "category": "road/civil", "project": "Demo Villas", "location": None, "quantity": 600, "uom": "sqm", "needed_by": "15 Dec", "missing": []}
    monkeypatch.setattr(engine, "parse", lambda t: dict(parsed))
    cid = engine.new_case("paver block 60 mm 600 sqm at Demo Villas"); engine.advance(cid)
    c = engine._case(cid)
    if c["state"] == "ITEM_REVIEW": engine.on_hod_action(cid, "item:pending")      # no road code list is loaded: HOD continues with code pending
    c = engine._case(cid); assert c["state"] == "STOCK_REVIEW" and c["waiting"] == "hod"
    before = db.con().execute("select sum(qty) from stock where material_code='MAT.PAV.60'").fetchone()[0]
    engine.on_hod_action(cid, "stock:move:Demo Greens:300")
    after = db.con().execute("select sum(qty) from stock where material_code='MAT.PAV.60'").fetchone()[0]
    assert before == after                                                         # the agent never moves stock
    mv = db.con().execute("select qty, status from stock_moves").fetchone(); assert mv["qty"] == 300 and "not moved" in mv["status"]
    c = engine._case(cid); assert c["state"] == "QUOTING" and c["data"]["parsed"]["quantity"] == 300   # RFQ for the balance


def test_engineer_stock_question_does_not_open_a_purchase_case():
    cid = engine.engineer_inbound("how much cement is left at Demo Heights?")
    c = engine._case(cid); assert c["state"] == "DECIDED"
    msgs = db.con().execute("select text from chat_messages where case_id=?", (cid,)).fetchall()
    assert msgs and "340" in msgs[0]["text"]


def test_supplies_check_in_the_comparison():
    found, missing = stock.supplies_check(["cement", "sand", "scaffolding", "water and electricity"], "Demo Heights")
    assert any("cement" in f for f in found) and any("scaffold" in f for f in found)


def test_whatsapp_style_cement_request_uses_the_material_catalogue_and_checks_stock_first(monkeypatch):
    monkeypatch.setattr(engine.mailer, "send", lambda *a, **k: "stub")
    parsed = {"item_description": "Portland cement OPC", "category": "other", "project": "Demo Greens", "location": None, "quantity": 200, "uom": "bags", "needed_by": "5 Dec", "missing": []}
    monkeypatch.setattr(engine, "parse", lambda t: dict(parsed))
    cid = engine.new_case("need 200 bags cement at Demo Greens by 5 Dec"); engine.advance(cid)
    c = engine._case(cid); assert c["data"]["item_code"] == "MAT.CEM.01" and c["state"] == "STOCK_REVIEW" and c["waiting"] == "hod"   # no 'code pending' question
    msg = db.con().execute("select text from chat_messages where case_id=? and to_role='hod'", (cid,)).fetchone()["text"]
    assert "Demo Heights" in msg and "transfer" in msg.lower()
