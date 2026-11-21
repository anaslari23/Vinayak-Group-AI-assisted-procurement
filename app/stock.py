"""Stock awareness from the master workbook: what is held where, what was used where. Answers questions and checks stock before a purchase.
Read-only: the agent recommends a transfer; it never moves stock."""
import re
from rapidfuzz import fuzz
from . import db

ASK = re.compile(r"\b(how much|how many|kitna|kitne|stock|left|balance|available|in store|remaining|bacha|kya hai|hold|holding)\b", re.I)
BUY = re.compile(r"\b(need|needed|require|required|want|chahiye|order|buy|purchase|supply|rfq|quote)\b", re.I)


def materials():
    c = db.con(); r = [dict(x) for x in c.execute("select * from materials")]; c.close(); return r


def find_material(text: str):
    """Best catalogue match: the longest synonym phrase found in the text wins ('tmt 16' beats 'tmt')."""
    t = " " + re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()) + " "; t = re.sub(r"\s+", " ", t)
    best = None
    for m in materials():
        for syn in [m["name"].lower()] + [s.strip().lower() for s in (m["synonyms"] or "").split(",") if s.strip()]:
            if re.search(r"\b" + re.escape(syn) + r"\b", t) or (len(syn) > 5 and fuzz.partial_ratio(syn, t) >= 92):
                if not best or len(syn) > best[0]: best = (len(syn), m)
    return best[1] if best else None


def site_of(text: str):
    c = db.con(); sites = [dict(r) for r in c.execute("select * from sites")]; c.close()
    t = (text or "").lower(); best = None
    for s in sites:
        for n in {s["name"], s["project"] or ""}:
            if n and (n.lower() in t or fuzz.partial_ratio(n.lower(), t) >= 88):
                if not best or len(n) > best[0]: best = (len(n), s["name"])
    return best[1] if best else None


def holdings(code: str, site: str | None = None):
    c = db.con()
    rows = [dict(r) for r in c.execute("select * from stock where material_code=? " + ("and site=? " if site else "") + "order by site, store", (code, site) if site else (code,))]; c.close()
    for r in rows: r["free"] = max(0.0, (r["qty"] or 0) - (r["reserved"] or 0))
    return rows


def usage(code: str, site: str | None = None, limit=3):
    c = db.con(); q = "select * from usage where material_code=? " + ("and site=? " if site else "") + "order by used_on desc"
    rows = [dict(r) for r in c.execute(q, (code, site) if site else (code,))]; c.close()
    return {"total": sum(r["qty"] for r in rows), "recent": rows[:limit]}


def _fmt(n):
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"


def answer(text: str):
    """A stock question in chat ('how much cement is left at Demo Heights?') answered from the workbook, or None if it is not one."""
    if not ASK.search(text) or BUY.search(text): return None
    m = find_material(text)
    if not m: return None
    site = site_of(text); hs = holdings(m["code"]); here = [h for h in hs if h["site"] == site] if site else []
    if not hs: return f"{m['name']}: nothing on record in any store. (Stock comes from the master workbook.)"
    lines = [f"{m['name']} ({m['unit']}):"]
    for h in (here or hs):
        lines.append(f"- {h['site']}, {h['store']}: {_fmt(h['qty'])} on hand, {_fmt(h['reserved'] or 0)} reserved, {_fmt(h['free'])} free (updated {h['updated']})")
    if site and here:
        others = [h for h in hs if h["site"] != site]
        if others: lines.append("Elsewhere: " + "; ".join(f"{h['site']} {_fmt(h['free'])} free" for h in others))
    u = usage(m["code"], site if here else None)
    if u["recent"]:
        lines.append("Recently used: " + "; ".join(f"{_fmt(r['qty'])} at {r['site']} {r['location']} ({r['used_on']})" for r in u["recent"]))
    lines.append("Source: master workbook (a recommendation tool; stock is not moved by me).")
    return "\n".join(lines)


def check_request(parsed: dict, raw: str = ""):
    """Before buying: is this already held, here or at another site? Returns None when there is nothing to say."""
    text = (parsed.get("item_description") or raw)
    m = find_material(text)
    if not m: return {"checked": False, "why": "not a catalogued stock material"}
    need = parsed.get("quantity"); uom = (parsed.get("uom") or "").lower(); site = site_of((parsed.get("project") or "") + " " + (parsed.get("location") or "") + " " + raw)
    hs = holdings(m["code"]); here = sum(h["free"] for h in hs if h["site"] == site) if site else 0
    others = {}
    for h in hs:
        if h["site"] != site and h["free"] > 0: others[h["site"]] = others.get(h["site"], 0) + h["free"]
    unit_ok = (not uom) or uom.rstrip("s") == (m["unit"] or "").lower().rstrip("s") or {uom, (m["unit"] or "").lower()} <= {"sqm", "sq m", "sq.m"}
    return {"checked": True, "material": m, "need": need, "uom": uom, "site": site, "here": here, "others": others, "unit_ok": unit_ok, "sites_checked": len({h["site"] for h in hs}), "holdings": hs}


def supplies_check(supplies: list[str], site: str | None):
    """Vendors expect the client to supply some items free of cost. Is each one in the site store?"""
    found, missing = [], []
    for s in supplies or []:
        m = find_material(s)
        if not m: continue
        free = sum(h["free"] for h in holdings(m["code"], site)) if site else 0
        (found if free > 0 else missing).append(f"{m['name'].split(' (')[0].lower()} ({_fmt(free)} {m['unit']} free)" if free > 0 else m["name"].split(" (")[0].lower())
    return found, missing
