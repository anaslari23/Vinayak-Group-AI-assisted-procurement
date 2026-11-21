"""Demo autopilot: in the real world vendors reply and time passes by themselves. For the synthetic demo there are no real vendors, so this stands in for them,
automatically: it sends the vendor quote emails, lets the agent chase and clarify, then moves the demo clock. Nobody presses anything; the only humans
in the loop are the ones the agent asks (the requester and the HOD). Switch off with DEMO_AUTOPILOT=0."""
import json, os, threading, time
from . import db, engine
from .tender import DATA_DIR, TENDER

ON = os.getenv("DEMO_AUTOPILOT", "1" if TENDER.get("synthetic") else "0") == "1"
_started: set = set()


def _run(cid):
    from . import main
    try:
        time.sleep(4)
        for f, key in main._demo_quotes():                                   # the vendors write back
            time.sleep(2)
            if not main._vendor_email(cid, key, "Re: SEEKING QUOTATION", "Please find our quotation attached.", f): engine.on_vendor_reply(cid, f, key)
        time.sleep(3); engine.advance_time(cid, 24)                           # a day passes: the agent chases silent vendors
        canned = json.loads((DATA_DIR / "followup_replies.json").read_text()) if (DATA_DIR / "followup_replies.json").exists() else {}
        d = engine._case(cid)["data"]
        for key, qs in (d.get("followups") or {}).items():                    # vendors answer the agent's questions
            if not qs or key in d.get("clarified", []): continue
            time.sleep(2); text = next((v for frag, v in canned.items() if frag in key), "Noted, we will confirm shortly.")
            if not main._vendor_email(cid, key, "Re: Clarification needed on your quotation", text): engine.on_vendor_text_reply(cid, key, text)
        time.sleep(4); engine.advance_time(cid, 24)                           # the deadline passes: the agent compares and asks the HOD
    except Exception as e:
        engine.log(cid, "system", "error", f"autopilot: {type(e).__name__}: {e}")


def _after_approval(cid):
    time.sleep(8); engine.advance_time(cid, 24 * 61)                          # the work gets done: the agent starts the post-job check-in


def tick():
    demo_tick()
    if not ON: return
    c = db.con(); rows = [dict(r) for r in c.execute("select id, state, data from cases where state in ('QUOTING','IN_WORK')")]; c.close()
    for r in rows:
        d = json.loads(r["data"] or "{}")
        if r["state"] == "QUOTING" and d.get("has_boq") and ("q", r["id"]) not in _started and not d.get("replied"):
            _started.add(("q", r["id"])); threading.Thread(target=_run, args=(r["id"],), daemon=True).start()
        if r["state"] == "IN_WORK" and ("w", r["id"]) not in _started:
            _started.add(("w", r["id"])); threading.Thread(target=_after_approval, args=(r["id"],), daemon=True).start()


# ---- hosted demo: no WhatsApp bridge there, so the demo plays the engineer and the admins too, and says so in the log. Real WhatsApp cases are never touched.
SAMPLES = ["Demo Heights Tower B toilet and balcony waterproofing, as per BOQ, needed by 20 Dec",
           "need 200 bags cement at Demo Villas by 15 Dec",
           "Demo Heights Tower B toilet and balcony waterproofing, as per BOQ, needed by 28 Dec"]
_state = {"i": 0, "last_end": 0.0, "started": 0.0}


def _bridge_open():
    try:
        from . import whatsapp as wa
        return wa.STATUS.get("state") == "open"
    except Exception:
        return False


def _decide(cid):
    """The admins' WhatsApp reply, simulated for the demo: the first option the agent offered."""
    time.sleep(14)
    c = engine._case(cid)
    if c["waiting"] not in ("hod", "engineer"): return
    cn = db.con(); m = cn.execute("select buttons from chat_messages where case_id=? and buttons is not null order by id desc limit 1", (cid,)).fetchone(); cn.close()
    opts = json.loads(m["buttons"]) if m and m["buttons"] else []
    if not opts: return
    who = "an admin" if c["waiting"] == "hod" else "the engineer"
    engine.log(cid, "system", "note", f"Demo: {who}'s WhatsApp reply is simulated here (the live system waits for a real person). Choosing: {opts[0]['label']}")
    engine.on_hod_action(cid, opts[0]["data"])


def demo_tick():
    if not ON or _bridge_open(): return
    c = db.con(); rows = [dict(r) for r in c.execute("select id, state, waiting, data from cases order by id desc limit 6")]; c.close()
    demo = [r for r in rows if (json.loads(r["data"] or "{}").get("channel") or "").startswith("demo")]
    now = time.time()
    for r in demo:
        if r["waiting"] in ("hod", "engineer") and ("d", r["id"], r["state"]) not in _started:
            _started.add(("d", r["id"], r["state"])); threading.Thread(target=_decide, args=(r["id"],), daemon=True).start()
    active = [r for r in demo if r["state"] != "DECIDED"]
    if not active:
        if not _state["last_end"]: _state["last_end"] = now - 100 if not rows else now
        if now - _state["last_end"] > 45 and now - _state["started"] > 45:
            text = SAMPLES[_state["i"] % len(SAMPLES)]; _state["i"] += 1; _state["started"] = now
            cid = engine.new_case(text, "demo (simulated engineer)"); engine.bg(engine.advance, cid)
    else:
        _state["last_end"] = 0.0
    if demo and not active and not _state["last_end"]: _state["last_end"] = now
