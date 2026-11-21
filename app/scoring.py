"""Step 5: deterministic weighted ranking. No LLM in the decision. Every point is traceable."""
from .db import match_vendor, history
from .normalise import normalise

W = {"price": 50, "performance": 30, "terms": 20}


def perf_score(hist: list[dict], w: int):
    if not hist:
        return w * 0.5, "No verified history for this vendor; neutral half-score applied (unverified)", None
    n = len(hist)
    q = sum(h["quality"] for h in hist) / n
    ot = sum(h["on_time"] for h in hist) / n
    disp = sum(h["disputes"] for h in hist)
    pts = w * (0.4 * q / 5 + 0.35 * ot + 0.25 * max(0.0, 1 - disp / n))
    return pts, f"{n} past jobs: quality {q:.1f}/5, on time {ot:.0%}, {disp} dispute(s)", {"n": n, "quality": q, "on_time": ot, "disputes": disp}


def terms_score(nq: dict, registered: bool, w: int):
    """20 pts: 5 quote current, 5 rates readable, 4 warranty stated, 3 payment terms stated, 3 registered vendor."""
    parts = []
    s = 0.0
    cur = 0 if nq["expired"] else (2 if nq["stale"] or nq["quote_age_days"] is None else 5); s += cur; parts.append(("quote current", cur, 5))
    rd = 5 * (1 - nq["low_conf_lines"] / 7); s += rd; parts.append(("rates clearly readable", round(rd, 1), 5))
    wr = 4 if nq["warranty_years"] else 0; s += wr; parts.append(("warranty stated", wr, 4))
    pt = 3 if nq["terms_stated"] else 0; s += pt; parts.append(("payment terms stated", pt, 3))
    rg = 3 if registered else 0; s += rg; parts.append(("registered vendor", rg, 3))
    return s * w / 20, parts


def rank(quotes: list[dict], weights: dict | None = None) -> list[dict]:
    w = weights or W
    rows = []
    for q in quotes:
        nq = normalise(q)
        v, sc = match_vendor(q["vendor_name"])
        key = v["key"] if v else None
        hist = history(key) if key else []
        rows.append({"q": q, "n": nq, "vendor_row": dict(v) if v else None, "registered": bool(v and v["registered"]), "hist": hist})
    lo = min(r["n"]["landed"] for r in rows)
    for r in rows:
        n = r["n"]
        p = w["price"] * lo / n["landed"]
        pf, pf_txt, pf_stats = perf_score(r["hist"], w["performance"])
        tm, tm_parts = terms_score(n, r["registered"], w["terms"])
        r.update(price_pts=p, perf_pts=pf, perf_txt=pf_txt, perf_stats=pf_stats, terms_pts=tm, terms_parts=tm_parts, total=p + pf + tm,
                 vs_lowest_pct=(n["landed"] / lo - 1) * 100)
    rows.sort(key=lambda r: -r["total"])
    for i, r in enumerate(rows, 1): r["rank"] = i
    po = sorted(rows, key=lambda r: r["n"]["landed"])
    for i, r in enumerate(po, 1): r["price_rank"] = i
    return rows


def reasons(r: dict, rows: list[dict]) -> list[str]:
    n, out = r["n"], []
    cheapest = min(rows, key=lambda x: x["n"]["landed"])
    if r is cheapest:
        out.append(f"Lowest landed cost: Rs {n['landed']:,.0f} (L1 on price).")
    else:
        out.append(f"Landed cost Rs {n['landed']:,.0f}, {r['vs_lowest_pct']:.0f}% above the lowest ({cheapest['n']['vendor']}, Rs {cheapest['n']['landed']:,.0f}).")
    out.append(r["perf_txt"] + (" [synthetic demo history]" if r["hist"] else "") + ".")
    if n["advance_pct"]:
        out.append(f"Asks {n['advance_pct']:g}% advance; modelled financing cost Rs {n['advance_cost']:,.0f}.")
    if n["expired"]:
        out.append(f"Quote is {n['quote_age_days']} days old against a {n['validity_days']}-day validity: needs re-confirmation.")
    elif n["stale"]:
        out.append(f"Quote is {n['quote_age_days']} days old: needs re-confirmation." if n["quote_age_days"] else "Quote carries no date.")
    if n["low_conf_lines"]:
        out.append(f"{n['low_conf_lines']} of 7 rates were read from handwriting/scan with low or medium confidence.")
    if not r["registered"]:
        out.append("Not in Vinayak's contractor list: vendor registration (SOP step 9) needed before a work order.")
    if n["qty_mismatch"]:
        out.append("Priced different quantities from the tender; re-priced at tender quantities.")
    if n["gst_assumed"]:
        out.append("GST rate not stated; 18% assumed.")
    if not n["terms_stated"]:
        out.append("Payment terms not stated; baseline assumed.")
    if n["warranty_years"]:
        out.append(f"Offers {n['warranty_years']:g}-year warranty.")
    if len(n["client_supplies"]) >= 8:
        out.append(f"Expects {len(n['client_supplies'])} client-supplied items free of cost (cost to Vinayak not priced here).")
    return out
