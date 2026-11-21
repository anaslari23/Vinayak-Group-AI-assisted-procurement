"""The agent: an event-driven case manager. One CASE per requisition, a state machine, and an event log of everything the agent does.
Deterministic control flow; Claude does the judgment steps (parse, read quotes, write the HOD note). The human is asked only when a
real decision is needed. There is no order or payment path anywhere in this module."""
import json, threading
from datetime import datetime, timedelta
from pathlib import Path
from rapidfuzz import fuzz
from . import db, items, rfq, mailer, recommend
from .extract import extract_quote
from .parse_req import parse, merge
from .scoring import rank, reasons, W
from .tender import TENDER, DATA_DIR

RFQ_HOURS = 48
_locks: dict = {}


def _lock(cid):
    return _locks.setdefault(cid, threading.RLock())


# ---------- persistence helpers
def _case(cid):
    c = db.con(); r = c.execute("select * from cases where id=?", (cid,)).fetchone(); c.close()
    d = dict(r); d["data"] = json.loads(d["data"] or "{}"); return d


def _save(cid, state=None, waiting="__keep__", data=None):
    c = db.con()
    if state is not None: c.execute("update cases set state=? where id=?", (state, cid))
    if waiting != "__keep__": c.execute("update cases set waiting=? where id=?", (waiting, cid))
    if data is not None: c.execute("update cases set data=? where id=?", (json.dumps(data), cid))
    c.execute("update cases set updated=CURRENT_TIMESTAMP where id=?", (cid,)); c.commit(); c.close()


def _ts():
    return datetime.now().isoformat(timespec="milliseconds")


def log(cid, actor, kind, text, payload=None):
    c = db.con(); c.execute("insert into events(case_id,actor,kind,text,payload,ts) values(?,?,?,?,?,?)", (cid, actor, kind, text, json.dumps(payload) if payload else None, _ts())); c.commit(); c.close()


def say(cid, to, text, buttons=None):
    """Queue a chat message for the engineer or the HOD. Front doors (simulator, Telegram) deliver it."""
    c = db.con(); c.execute("insert into chat_messages(case_id,to_role,text,buttons,ts) values(?,?,?,?,?)", (cid, to, text, json.dumps(buttons) if buttons else None, _ts())); c.commit(); c.close()
    log(cid, "agent", "message", f"→ {to}: {text}")


def now(case):
    return datetime.now() + timedelta(hours=case["clock_offset_h"] or 0)


def new_case(raw: str, channel="web") -> int:
    c = db.con(); cur = c.execute("insert into cases(state,waiting,data) values('NEW',NULL,?)", (json.dumps({"raw": raw, "channel": channel, "clarify_rounds": 0}),)); cid = cur.lastrowid; c.commit(); c.close()
    log(cid, "engineer", "received", raw, {"channel": channel})
    return cid


def quotes_of(cid):
    c = db.con(); r = [json.loads(x["extracted"]) for x in c.execute("select extracted from quotes where case_id=?", (cid,))]; c.close(); return r


# ---------- decisions the agent makes in code
def find_boq(parsed: dict, raw: str):
    """Does the agent already hold a BOQ package for this project? (matched by name, not by a button click)"""
    if parsed.get("category") != "waterproofing": return None
    hay = " ".join(str(parsed.get(k) or "") for k in ("project", "location")) + " " + raw
    return TENDER if fuzz.partial_ratio(TENDER["project"].lower(), hay.lower()) >= 85 else None


def missing(parsed: dict, boq) -> list[str]:
    m = []
    if not boq:
        if not parsed.get("quantity"): m.append("quantity")
        if not parsed.get("uom"): m.append("unit (sqft / sqm / rmt / nos)")
    if not (parsed.get("project") or parsed.get("location")): m.append("project / location")
    if not parsed.get("needed_by"): m.append("needed-by date")
    return m


def shortlist(parsed: dict) -> list[dict]:
    cat = parsed.get("category") or "other"
    c = db.con(); rows = [dict(r) for r in c.execute("select * from vendors where category=? order by registered desc, name", (cat,))]; c.close()
    return rows


# ---------- state handlers: return True if the case moved on, False if it is waiting
def h_new(c):
    cid, d = c["id"], c["data"]
    parsed = parse(d["raw"]); d["parsed"] = parsed
    boq = find_boq(parsed, d["raw"]); d["has_boq"] = bool(boq)
    log(cid, "agent", "parsed", f"Structured: {parsed.get('item_description')} | {parsed.get('category')} | qty {parsed.get('quantity') or '?'} {parsed.get('uom') or ''} | needed by {parsed.get('needed_by') or '?'}", parsed)
    if boq: log(cid, "agent", "context", f"I already hold the BOQ package for {boq['project']} ({boq['package']}), so quantities come from there.")
    _save(cid, "CLARIFY", None, d); return True


def h_clarify(c):
    cid, d = c["id"], c["data"]; m = missing(d["parsed"], d["has_boq"])
    if m and d["clarify_rounds"] < 2:
        if not d.get("asked_open"):
            say(cid, "engineer", f"Got it: {d['parsed'].get('item_description')}. To raise the enquiry I still need: {', '.join(m)}. Reply in one message.")
            log(cid, "agent", "asked", "Asked the engineer for: " + ", ".join(m)); d["asked_open"] = True; _save(cid, waiting="engineer", data=d)
        return False
    if m:
        log(cid, "agent", "note", "Proceeding with assumptions; still unknown: " + ", ".join(m))
    _save(cid, "MATCH", None, d); return True


def h_match(c):
    cid, d = c["id"], c["data"]
    if d.get("has_boq"):  # the requirement is a whole BOQ package: check each BOQ line's code against the master instead of asking anyone
        ok, vari = 0, set()
        for tl in TENDER["lines"]:
            m = items.match(tl["title"]); top = m["candidates"][0]
            ok += top["code"] == tl["item_code"]; vari |= {x["code"] for x in m["duplicates_of_best"]}
        d["item_code"] = "BOQ package " + ", ".join(l["item_code"] for l in TENDER["lines"][:1]) + "..."
        log(cid, "agent", "matched", f"BOQ package: {ok} of {len(TENDER['lines'])} line codes confirmed against the item master. A/B variants and near-duplicates flagged for later clean-up: {', '.join(sorted(vari)[:6])}. No human needed.")
        _save(cid, "RFQ", None, d); return True
    m = items.match(d["parsed"].get("item_description") or d["raw"]); d["match"] = m
    top = m["candidates"][0] if m["candidates"] else None
    dups = ", ".join(x["code"] for x in m["duplicates_of_best"]) or "none"
    if m["verdict"] == "match":
        d["item_code"] = top["code"]
        log(cid, "agent", "matched", f"Confident match: {top['code']} ({top['descr'][:60]}). Near-duplicates / variants: {dups}. No human needed.", {"score": top["score"]})
        _save(cid, "RFQ", None, d); return True
    opts = [{"label": f"Use {x['code']}", "data": f"item:{x['code']}"} for x in m["candidates"][:2]] + [{"label": f"Create new ({m.get('suggested_code', {}).get('code', 'new code')})", "data": "item:new"}]
    say(cid, "hod", f"Item code needs your call for: {d['parsed'].get('item_description')}.\nBest candidates: " + "; ".join(f"{x['code']} ({x['score']})" for x in m["candidates"][:2]) + f". Possible duplicates: {dups}.", opts)
    log(cid, "agent", "escalated", f"Match was {m['verdict']} (not confident), so I asked the HOD to choose the item code.")
    _save(cid, "ITEM_REVIEW", "hod", d); return False


def h_item_review(c): return False


def h_rfq(c):
    cid, d = c["id"], c["data"]
    vs = shortlist(d["parsed"]); sent = []
    if len(vs) < 3: log(cid, "agent", "note", f"Only {len(vs)} vendors in this category; SOP asks for at least 3.")
    for v in vs:
        subj, body = rfq.draft(v["name"]) if d["has_boq"] else rfq.draft_generic(v["name"], d["parsed"], d.get("item_code"))
        try:
            st = mailer.send("rfq", v["test_email"], subj, body); sent.append(v["key"])
        except mailer.RecipientBlocked as e:
            st = f"BLOCKED: {e}"
        log(cid, "agent", "rfq_sent", f"RFQ to {v['name']} ({'registered' if v['registered'] else 'NOT registered, needs vendor registration'}): {st}")
    d["shortlist"] = sent; d["deadline"] = (now(c) + timedelta(hours=RFQ_HOURS)).isoformat()
    say(cid, "engineer", f"RFQ sent to {len(sent)} vendors. I will read their replies, chase anyone silent, and bring the HOD a ranked recommendation. Nothing more is needed from you.")
    _save(cid, "QUOTING", "vendors", d); return True


def h_quoting(c):
    cid, d = c["id"], c["data"]
    if not d["has_boq"]: return False  # no BOQ package: generic quote reading arrives in a later phase
    got = quotes_of(cid); due = now(c) >= datetime.fromisoformat(d["deadline"])
    if len(got) >= len(d["shortlist"]) or (due and len(got) >= 2):
        if len(got) < len(d["shortlist"]):
            silent = [v for v in d["shortlist"] if v not in d.get("replied", [])]
            log(cid, "agent", "note", f"Deadline passed. Comparing the {len(got)} quotes received; {len(silent)} vendor(s) did not reply.")
        _save(cid, "COMPARE", None, d); return True
    return False


def h_compare(c):
    cid, d = c["id"], c["data"]
    rows = rank(quotes_of(cid), W)
    for r in rows: r["reasons"] = reasons(r, rows)
    summ, how = recommend.summary(rows); top = rows[0]
    log(cid, "agent", "compared", "Comparative statement built: " + "; ".join(f"#{r['rank']} {r['n']['vendor']} Rs {r['n']['landed']:,.0f} ({r['total']:.1f})" for r in rows), {"how": how})
    tok = recommend.sign(f"case-{cid}", top["n"]["vendor"])
    import os
    link = f"{os.getenv('PUBLIC_URL', 'http://localhost:8000')}/cases/{cid}"
    text = f"Recommendation for {TENDER['project']} {TENDER['package']}:\n{summ}\n\nFull statement and reasons: {link}\nThis is a recommendation only; you decide."
    d["recommended"] = top["n"]["vendor"]
    say(cid, "hod", text, [{"label": f"Approve {top['n']['vendor']}", "data": "decision:approve"}, {"label": "Send back for re-quote", "data": "decision:send back"}, {"label": "Reject", "data": "decision:reject"}])
    try: mailer.send("hod", mailer.HOD_ADDR, f"Vendor recommendation for approval: {TENDER['package']}", text + f"\nReview link: {link}\nSigned token: {tok}")
    except mailer.RecipientBlocked: pass
    log(cid, "agent", "recommended", f"Recommendation sent to the HOD: {top['n']['vendor']}. Waiting for the human decision.")
    _save(cid, "AWAITING_HOD", "hod", d); return False


HANDLERS = {"NEW": h_new, "CLARIFY": h_clarify, "MATCH": h_match, "ITEM_REVIEW": h_item_review, "RFQ": h_rfq, "QUOTING": h_quoting, "COMPARE": h_compare, "AWAITING_HOD": lambda c: False, "DECIDED": lambda c: False}


def advance(cid, max_steps=12):
    with _lock(cid):
        for _ in range(max_steps):
            c = _case(cid)
            try:
                if not HANDLERS[c["state"]](c): return
            except Exception as e:  # never die silently: the human sees what went wrong
                log(cid, "system", "error", f"{type(e).__name__}: {e}"); _save(cid, waiting="human (error)"); return


# ---------- events from outside
def on_engineer_message(cid, text):
    with _lock(cid):
        c = _case(cid); d = c["data"]
        log(cid, "engineer", "received", text)
        if c["state"] == "CLARIFY" and d.get("asked_open"):
            d["parsed"] = merge(d["parsed"], text); d["clarify_rounds"] += 1; d["asked_open"] = False
            boq = find_boq(d["parsed"], d["raw"] + " " + text); d["has_boq"] = bool(boq)
            _save(c["id"], waiting=None, data=d); log(cid, "agent", "parsed", "Updated requirement from the reply.", d["parsed"])
    advance(cid)


def on_hod_action(cid, action: str):
    with _lock(cid):
        c = _case(cid); d = c["data"]; kind, _, val = action.partition(":")
        log(cid, "hod", "action", f"HOD chose: {action}")
        if kind == "item" and c["state"] == "ITEM_REVIEW":
            d["item_code"] = (d["match"].get("suggested_code", {}).get("code") if val == "new" else val)
            log(cid, "agent", "decided_item", f"Item code set to {d['item_code']} by the HOD" + (" (new code, flagged ASSUMED numbering)" if val == "new" else "")); _save(cid, "RFQ", None, d)
        elif kind == "decision" and c["state"] == "AWAITING_HOD":
            cn = db.con(); cn.execute("insert into decisions(tender,decision,note,recommended) values(?,?,?,?)", (f"case-{cid}", val, "via chat", d.get("recommended"))); cn.commit(); cn.close()
            if val == "send back":
                d["deadline"] = (now(c) + timedelta(hours=RFQ_HOURS)).isoformat(); _save(cid, "QUOTING", "vendors", d)
                log(cid, "agent", "note", "HOD sent it back. Re-opened quoting for 48 h; I will chase vendors for revised quotes.")
            else:
                _save(cid, "DECIDED", None, d); say(cid, "engineer", f"HOD decision on {d.get('recommended')}: {val}. (Recommendation only; ordering stays with Vinayak.)")
                log(cid, "agent", "closed", f"Case closed with HOD decision: {val}.")
    advance(cid)


def extract_any(path: Path):
    """Fixture mode: known demo files use cached extraction (instant); anything else runs the live extractor."""
    for sub in ("cache", "eval_cache"):
        f = DATA_DIR / sub / (path.stem + ".json")
        if f.exists(): return json.loads(f.read_text()), "cached extraction (fixture)"
    return extract_quote(path), "live extraction"


def on_vendor_reply(cid, path: Path):
    with _lock(cid):
        q, how = extract_any(path); v, _ = db.match_vendor(q["vendor_name"]); key = v["key"] if v else q["vendor_name"]
        c = db.con(); c.execute("insert or replace into quotes(case_id,vendor_key,source_file,extracted) values(?,?,?,?)", (cid, key, path.name, json.dumps(q))); c.commit(); c.close()
        cs = _case(cid); d = cs["data"]; d.setdefault("replied", []); d["replied"] = list(set(d["replied"] + [key])); _save(cid, data=d)
        from .normalise import normalise
        n = normalise(q)
        log(cid, f"vendor:{q['vendor_name']}", "reply_received", f"Quote received: {path.name}")
        log(cid, "agent", "extracted", f"Read the quote ({how}): landed Rs {n['landed']:,.0f} at tender quantities; {len(n['flags'])} flag(s): " + " | ".join(n["flags"][:3]), {"flags": n["flags"]})
    advance(cid)


def advance_time(cid, hours: float):
    with _lock(cid):
        c = db.con(); c.execute("update cases set clock_offset_h = clock_offset_h + ? where id=?", (hours, cid)); c.commit(); c.close()
        log(cid, "system", "clock", f"Demo clock moved forward {hours:g} h")
    advance(cid)


def bg(fn, *a):
    t = threading.Thread(target=fn, args=a, daemon=True); t.start(); return t


def timeline(cid):
    c = db.con()
    ev = [dict(r) for r in c.execute("select * from events where case_id=? order by id", (cid,))]
    ch = [dict(r) for r in c.execute("select * from chat_messages where case_id=? order by id", (cid,))]; c.close()
    for m in ch: m["buttons"] = json.loads(m["buttons"]) if m["buttons"] else []
    return ev, ch


def engineer_inbound(text: str, channel="web", cid=None) -> int:
    """One entry point for every front door. A reply to an open question continues that case; anything else opens a new case."""
    if cid:
        c = _case(cid)
        if c["state"] == "CLARIFY" and c["data"].get("asked_open"):
            on_engineer_message(cid, text); return cid
    cid = new_case(text, channel); advance(cid); return cid


def conversation(cid, role):
    """Chat bubbles for one person in a case, oldest first: what the agent told them and what they said."""
    c = db.con()
    out = [{"who": "agent", "text": m["text"], "buttons": json.loads(m["buttons"]) if m["buttons"] else [], "ts": m["ts"]} for m in c.execute("select * from chat_messages where case_id=? and to_role=?", (cid, role))]
    out += [{"who": "me", "text": e["text"], "buttons": [], "ts": e["ts"]} for e in c.execute("select * from events where case_id=? and actor=? and kind in ('received','action')", (cid, role))]
    c.close(); return sorted(out, key=lambda x: x["ts"])
