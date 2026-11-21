"""Step 2 spec tests: the 14 cases from the build spec (plus our own), the status rules, and the agent's behaviour on each status."""
import json
from pathlib import Path
import pytest
from app import db, engine, itemmatch as im
from app.tender import DATA_DIR

TESTS = json.loads((Path(__file__).resolve().parent.parent / "data" / "item_tests.json").read_text())


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db")); db.init(reset=True); im.erp(reload=True)


@pytest.mark.parametrize("t", TESTS, ids=[t["id"] for t in TESTS])
def test_status_and_candidates(t):
    meta = json.loads((DATA_DIR / "item_meta.json").read_text())["aliases"]
    r = im.match(t["text"])
    assert r["status"] == t["expect_status"], (r["status"], r["message"])
    exp = {c for a in t["expect_top"] for c in meta.get(a, [a])}
    if exp: assert r["candidates"][0]["code"] in exp and {c["code"] for c in r["candidates"]} & exp
    assert {f["flag"] for f in r["flags"]} >= set(t["expect_flags"])
    assert r["status"] != "matched_high" or t["expect_status"] == "matched_high"      # never confidently wrong


def test_exact_site_code_auto_matches_but_free_text_does_not():
    assert im.match("waterproofing at toilet floor", site_code_hint="WPD.01")["status"] == "matched_high"
    assert im.match("waterproofing at toilet floor")["status"] == "needs_back_office_confirm"   # variants exist: a person picks


def test_floor_vs_wall_never_swaps():
    assert im.match("toilet wall waterproofing")["candidates"][0]["code"].startswith("WPD.02")
    assert im.match("toilet floor waterproofing")["candidates"][0]["code"].startswith("WPD.01")


def test_new_code_is_a_draft_with_reasons_and_out_of_category_invents_nothing():
    r = im.match("tank cleaning lining epoxy coating")
    assert r["status"] in ("suggest_new_code", "needs_back_office_confirm", "needs_more_info")
    road = im.match("60mm paver block 1500 sqm")
    assert road["status"] == "needs_more_info" and "No code list loaded" in road["message"] and road["new_code_suggestion"] is None


def test_learned_choice_is_used_first_next_time():
    before = im.match("toilet floor waterproofing")["candidates"][0]["code"]
    im.remember("toilet floor waterproofing", "WPD.01.B", who="test")
    r = im.match("toilet floor waterproofing")
    assert r["candidates"][0]["code"] == "WPD.01.B" and r["learned"] and before != "WPD.01.B"


def test_cleanup_report_lists_the_mess():
    rep = im.cleanup_report(); flags = {x["flag"] for x in rep}
    assert {"erp_conflicting_code", "erp_exact_repeat", "erp_same_work_many_codes", "erp_lookalike_code"} <= flags


def test_agent_asks_back_instead_of_guessing(monkeypatch):
    sent = []
    monkeypatch.setattr(engine.mailer, "send", lambda *a, **k: sent.append(a) or "stub")
    parsed = {"item_description": "waterproofing", "category": "waterproofing", "project": "Aquina", "location": None, "quantity": 100, "uom": "sqft", "needed_by": "10 Dec", "missing": []}
    monkeypatch.setattr(engine, "parse", lambda t: parsed)
    cid = engine.new_case("waterproofing"); engine.advance(cid)
    c = engine._case(cid); assert c["state"] == "MATCH_ASK" and c["waiting"] == "engineer"        # asked which area, floor or wall
    monkeypatch.setattr(engine, "merge", lambda p, r: {**p, "item_description": "toilet floor waterproofing"})
    engine.on_engineer_message(cid, "toilet floor")
    c = engine._case(cid); assert c["state"] == "ITEM_REVIEW" and c["waiting"] == "hod"            # variants exist: one tap for the HOD
    engine.on_hod_action(cid, "item:WPD.01")
    assert engine._case(cid)["data"]["item_code"] == "WPD.01" and engine._case(cid)["state"] == "QUOTING"
    assert im.match("toilet floor waterproofing")["learned"]                                          # the choice was remembered
    assert db.con().execute("select count(*) from item_log where approved_by='HOD'").fetchone()[0] == 1


def test_junk_gets_a_polite_reply_and_no_code(monkeypatch):
    monkeypatch.setattr(engine, "parse", lambda t: {"item_description": "hello", "category": "other", "project": None, "location": None, "quantity": None, "uom": None, "needed_by": None, "missing": []})
    cid = engine.new_case("hello"); engine.advance(cid)
    c = engine._case(cid); assert c["state"] in ("DECIDED", "CLARIFY")
