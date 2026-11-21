"""Live flow: turns a case and its event log into the pipeline the UI animates. Nothing here is canned: every step, check and number is read from what the agent actually did."""
import json, re
from . import db, engine
from .tender import TENDER

STEPS = [("capture", "Capture the requirement"), ("clarify", "Ask and confirm"), ("item", "Match the item code"), ("stock", "Check stock"), ("rfq", "Send RFQs"),
         ("quotes", "Read vendor quotes"), ("compare", "Compare and rank"), ("hod", "Admins decide"), ("outcome", "Outcome and vendor record")]
ORDER = {"NEW": 0, "CLARIFY": 1, "CONFIRM": 1, "MATCH": 2, "MATCH_ASK": 2, "ITEM_REVIEW": 2, "STOCK": 3, "STOCK_REVIEW": 3, "RFQ": 4, "QUOTING": 5, "COMPARE": 6,
         "AWAITING_HOD": 7, "IN_WORK": 8, "CHECKIN": 8, "DECIDED": 9}


def _ev(cid):
    c = db.con(); r = [dict(x) for x in c.execute("select * from events where case_id=? order by id", (cid,))]; c.close(); return r


def _ck(ok, text, **kw): return {"ok": ok, "text": text, **kw}


ACTIVE_MIN = 30  # a case with no event for this long is history, not "live"


def ages():
    """Seconds since each case's last event (same process clock that wrote the events)."""
    from datetime import datetime
    c = db.con(); rows = c.execute("select case_id, max(ts) t from events group by case_id").fetchall(); c.close(); out = {}
    for r in rows:
        try: out[r["case_id"]] = (datetime.now() - datetime.fromisoformat(r["t"].replace(" ", "T"))).total_seconds()
        except Exception: pass
    return out


def latest_case():
    a = {k: v for k, v in ages().items() if v < ACTIVE_MIN * 60}
    return max(a) if a else None


def build(cid):
    c = engine._case(cid); d = c["data"]; ev = _ev(cid); state = c["state"]; cur = ORDER.get(state, 0); parsed = d.get("parsed") or {}; boq = d.get("has_boq")
    by = lambda *k: [e for e in ev if e["kind"] in k]
    steps = {k: {"key": k, "title": t, "status": "pending", "summary": "", "checks": [], "data": None} for k, t in STEPS}

    # 1 capture
    s = steps["capture"]
    if parsed:
        s["summary"] = f"{parsed.get('item_description') or 'requirement'}"
        s["data"] = {"kv": [("Item", parsed.get("item_description")), ("Category", parsed.get("category")), ("Quantity", (f"{parsed.get('quantity')} {parsed.get('uom') or ''}".strip() if parsed.get("quantity") else ("from the BOQ on file" if boq else None))),
                            ("Site / project", parsed.get("project") or parsed.get("location")), ("Needed by", parsed.get("needed_by"))]}
        s["checks"] = [_ck(bool(parsed.get("item_description")), "Understood what is needed"), _ck(bool(parsed.get("quantity") or boq), "Quantity known" + (" (from the BOQ package on file)" if boq and not parsed.get("quantity") else "")),
                       _ck(bool(parsed.get("project") or parsed.get("location")), "Site identified"), _ck(bool(parsed.get("needed_by")), "Needed-by date given")]
    # 2 clarify / confirm
    s = steps["clarify"]; asked = by("asked"); conf = d.get("confirmed")
    s["checks"] = [_ck(None, e["text"][:140]) for e in asked if "Item match" not in e["text"] and "Work period" not in e["text"]]
    if conf: s["checks"].append(_ck(True, "Requester confirmed the read-back"))
    if not s["checks"] and cur > 1: s["checks"] = [_ck(True, "Nothing missing, no question needed")]
    s["summary"] = "Waiting for the requester" if state in ("CLARIFY", "CONFIRM") and c["waiting"] == "engineer" else ("Complete" if cur > 1 else "")
    # 3 item code
    s = steps["item"]
    for e in by("matched", "escalated", "decided_item"):
        if "stock" in e["text"].lower() and "Stock check" in e["text"]: continue
        s["checks"].append(_ck(True if e["kind"] in ("matched", "decided_item") else None, e["text"][:200]))
    m = d.get("match") or {}
    if d.get("item_code"): s["summary"] = str(d["item_code"])
    if m.get("candidates"):
        s["data"] = {"table": {"head": ["Code", "Description", "Confidence"], "rows": [[x["code"], x["description"][:46], x["confidence"]] for x in m["candidates"][:3]]}}
    # 4 stock
    s = steps["stock"]
    if boq and cur > 3: s["status"] = "skipped"; s["summary"] = "A works package: no stock check needed"
    for e in by("stock"): s["checks"].append(_ck(True, e["text"][:220]))
    sm = [e for e in by("escalated") if "Stock" in e["text"]]
    for e in sm: s["checks"].append(_ck(None, e["text"][:220]))
    # 5 rfq
    s = steps["rfq"]; rf = by("rfq_sent")
    for e in rf:
        mm = re.match(r"RFQ to (.+?) \((.+?)\): (.+)", e["text"])
        if mm: s["checks"].append(_ck("BLOCKED" not in mm.group(3), f"{mm.group(1)}: {mm.group(3)[:60]}", tag=("registered" if mm.group(2).startswith("registered") else "not registered")))
    s["summary"] = f"{len(rf)} vendors asked, test inboxes only" if rf else ""
    # 6 quotes
    s = steps["quotes"]
    from .normalise import normalise
    rows = []
    c2 = db.con(); qs = [json.loads(x["extracted"]) for x in c2.execute("select extracted from quotes where case_id=?", (cid,))]; c2.close()
    for q in qs:
        n = normalise(q); rows.append([n["vendor"][:28], f"Rs {n['landed']:,.0f}", f"{len(n['flags'])} flags", "stale" if (n["expired"] or n["stale"]) else "current"])
    if rows: s["data"] = {"table": {"head": ["Vendor", "Landed cost", "Open points", "Quote"], "rows": rows}}
    for kind, label in (("chased", None), ("followup", None), ("updated", None)):
        for e in by(kind): s["checks"].append(_ck(True if kind == "updated" else None, e["text"][:200]))
    s["summary"] = f"{len(rows)} quotes read" if rows else ""
    # 7 compare
    s = steps["compare"]
    if qs and cur >= 6:
        try:
            from .scoring import rank, reasons, W
            rk = rank(qs, W); lo = rk[0]["total"] if rk else 100
            s["data"] = {"bars": [{"label": r["n"]["vendor"][:26], "value": round(r["total"], 1), "price": round(r["price_pts"], 1), "hist": round(r["perf_pts"], 1), "terms": round(r["terms_pts"], 1), "note": reasons(r, rk)[0][:110]} for r in rk]}
            s["summary"] = f"Top: {rk[0]['n']['vendor'][:30]} ({rk[0]['total']:.1f}/100)"
            s["checks"] = [_ck(True, "Every quote re-priced at the tender quantities"), _ck(True, "Ranked on price, vendor history and terms; reasons shown for each")]
            if any(r["n"]["gst_assumed"] for r in rk): s["checks"].append(_ck(None, "GST assumed at 18% where a vendor did not state it"))
        except Exception as e:
            s["checks"] = [_ck(None, "Ranking unavailable: " + str(e)[:60])]
    # 8 hod
    s = steps["hod"]
    c3 = db.con(); hm = c3.execute("select text, buttons from chat_messages where case_id=? and to_role='hod' order by id desc limit 1", (cid,)).fetchone(); c3.close()
    acts = [e for e in ev if e["kind"] == "action" and e["actor"] == "hod"]
    if hm and cur >= 7:
        s["summary"] = acts[-1]["text"].replace("HOD chose: ", "Decision: ") if acts and state != "AWAITING_HOD" else "Waiting for the admins"
        s["data"] = {"message": hm["text"][:420], "options": json.loads(hm["buttons"]) if (hm["buttons"] and c["waiting"] == "hod") else []}
    s["checks"] = [_ck(None if c["waiting"] == "hod" else True, "The agent recommends; a person decides. No order or payment is ever made.")] if cur >= 7 else []
    # 9 outcome
    s = steps["outcome"]
    for e in by("ledger", "closed", "stock"):
        if e["kind"] == "stock" and "Transfer request" not in e["text"]: continue
        s["checks"].append(_ck(True, e["text"][:200]))

    # stock: re-run the deterministic check so the panel shows the real holdings per site
    if not boq and parsed and cur >= 3:
        try:
            from . import stock as stockmod
            chk = stockmod.check_request(parsed, d.get("raw", ""))
            if chk.get("checked"):
                u = chk["material"]["unit"]; steps["stock"]["data"] = {"kv": [("Material", chk["material"]["name"]), ("Needed", f"{chk['need'] or '?'} {chk['uom'] or ''}"), ("Site", chk["site"])],
                    "table": {"head": ["Site", f"Free ({u})"], "rows": [[chk["site"] or "this site", f"{chk['here']:,.0f}"]] + [[k, f"{v:,.0f}"] for k, v in chk["others"].items()]}}
                steps["stock"]["sub"] = f"{chk['here']:,.0f} here · {sum(chk['others'].values()):,.0f} elsewhere"
        except Exception: pass

    # the short line on each node: always from this case's data, never a fixed caption
    q = parsed.get("quantity"); qty = f"{q:g} {parsed.get('uom') or ''}".strip() if isinstance(q, (int, float)) else (str(q) if q else ("BOQ on file" if boq else ""))
    sub = {"capture": " · ".join(x for x in [" ".join((parsed.get("item_description") or "").split()[:2])[:14], qty] if x) if parsed else "reading…",
           "clarify": "confirmed" if conf else (f"asked {len(asked)}" if asked else ("nothing missing" if cur > 1 else "")),
           "item": str(d.get("item_code") or ""), "rfq": f"{len(rf)} vendors asked" if rf else "",
           "quotes": f"{len(rows)} of {len(rf) or len(rows)} replied" if rows else (f"0 of {len(rf)} replied" if rf else ""),
           "compare": (steps["compare"]["summary"].replace("Top: ", "")[:22]) if steps["compare"]["summary"] else "",
           "hod": ("waiting" if c["waiting"] == "hod" else (acts[-1]["text"].split(": ", 1)[-1][:22] if acts else "")) if cur >= 7 else "",
           "outcome": "closed" if state == "DECIDED" else ("in work" if state in ("IN_WORK", "CHECKIN") else "")}
    if steps["stock"]["status"] == "skipped": sub["stock"] = "not needed (BOQ)"
    for k, v in sub.items(): steps[k].setdefault("sub", v) if v else None
    for k in steps: steps[k].setdefault("sub", "")

    # statuses
    keys = [k for k, _ in STEPS]
    for i, k in enumerate(keys):
        st = steps[k]
        if st["status"] == "skipped": continue
        if state == "DECIDED": st["status"] = "done" if (st["checks"] or st["summary"] or st["data"] or i <= 1) else "skipped"
        elif i < cur: st["status"] = "done" if (st["checks"] or st["summary"] or st["data"] or i == 0) else "skipped"
        elif i == cur: st["status"] = "waiting" if c["waiting"] in ("engineer", "hod") else "active"
        else: st["status"] = "pending"
    if state == "DECIDED":
        steps["outcome"]["status"] = "done"
    vend = {}
    def vk(n): return re.sub(r"[^a-z]", "", n.lower())[:7]
    for e in rf:
        mm = re.match(r"RFQ to (.+?) \((.+?)\): (.+)", e["text"])
        if mm: vend[vk(mm.group(1))] = {"name": mm.group(1)[:20], "state": "asked", "registered": mm.group(2).startswith("registered")}
    for q in qs:
        v = vend.get(vk(q["vendor_name"])) or vend.setdefault(vk(q["vendor_name"]), {"name": q["vendor_name"][:20], "state": "asked", "registered": True})
        n = normalise(q); v["state"] = "replied"; v["landed"] = f"Rs {n['landed']/100000:,.1f} L"
    for e in by("chased"):
        for k, v in vend.items():
            if v["name"][:8].lower() in e["text"].lower() and v["state"] == "asked": v["state"] = "chased"
    chat = []
    for e in ev:
        if e["kind"] == "received" and e["actor"] in ("engineer", "hod"):
            chat.append({"who": e["actor"], "text": e["text"][:300], "ts": e["ts"][11:16]})
        elif e["kind"] == "message" and e["text"].startswith("→"):
            m = re.match(r"→ (\w+): (.*)", e["text"], re.S)
            if m: chat.append({"who": "agent", "to": m.group(1), "text": m.group(2)[:420], "ts": e["ts"][11:16]})
        elif e["kind"] == "action" and e["actor"] in ("hod", "engineer"):
            chat.append({"who": e["actor"], "text": e["text"].replace("HOD chose: ", "").replace("Engineer chose: ", "")[:120], "ts": e["ts"][11:16], "choice": True})
        elif e["kind"] == "note" and e["text"].startswith("Demo:"):
            chat.append({"who": "demo", "text": e["text"][:200], "ts": e["ts"][11:16]})
    err = [e for e in ev if e["kind"] == "error"]
    return {"case_id": cid, "state": state, "label": engine.STATE_TEXT.get(state, state), "waiting": c["waiting"], "raw": d.get("raw", ""), "channel": d.get("channel", ""), "error": err[-1]["text"] if err else None,
            "steps": [steps[k] for k in keys], "vendors": list(vend.values()), "chat": chat[-14:], "events": [{"actor": e["actor"], "kind": e["kind"], "text": e["text"][:160], "ts": e["ts"][11:19]} for e in ev[-6:]], "project": TENDER["project"], "age": round(ages().get(cid, 0))}
