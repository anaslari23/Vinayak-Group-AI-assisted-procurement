"""Step 5: deterministic weighted ranking. No LLM in the decision. Every point is traceable."""
from .db import match_vendor, history, signals
from .normalise import normalise

W = {"price": 50, "performance": 30, "terms": 20}


def _age_weight(job_date, today):
    """Recent jobs count more: weight halves every year."""
    from datetime import date
    if not job_date: return 0.5
    return 0.5 ** (max(0, (today - date.fromisoformat(job_date[:10])).days) / 365)


def _blend(x, conf):
    """Pull toward the neutral 0.5 when there is little evidence."""
    return 0.5 + (x - 0.5) * conf


def perf_score(hist: list[dict], sigs: list[dict], w: float):
    """Vendor performance out of w points: 2/3 from the job record (quality, delivery, disputes, recency-weighted),
    1/3 from how they behave in the buying process (answer rate, chasers, quote completeness, price discipline).
    Too little evidence pulls the score toward neutral and is reported as such."""
    from .normalise import ASSUMP
    today = ASSUMP["today"]
    parts, ev = [], []
    if hist:
        ws = [_age_weight(h.get("job_date"), today) for h in hist]; tw = sum(ws)
        q = sum(h["quality"] * x for h, x in zip(hist, ws)) / tw; ot = sum(h["on_time"] * x for h, x in zip(hist, ws)) / tw
        dr = sum(min(1, h["disputes"]) * x for h, x in zip(hist, ws)) / tw
        job = 0.4 * q / 5 + 0.35 * ot + 0.25 * (1 - dr); conf_j = min(1.0, tw / 2.5)
        stats = {"n": len(hist), "quality": q, "on_time": ot, "dispute_rate": dr}
        ev.append(f"{len(hist)} past job(s): quality {q:.1f}/5, on time {ot:.0%}, disputes on {dr:.0%} of jobs (recent jobs weigh more)")
    else:
        job, conf_j, stats = 0.5, 0.0, None
        ev.append("no job history on record")
    if sigs:
        n = len(sigs); answered = [x for x in sigs if x["responded"]]
        ans = len(answered) / n; chased = sum(x["chased"] for x in sigs) / n
        comp = 1 - min(1, (sum((x["issues"] or 0) for x in answered) / max(1, len(answered))) / 3)
        price = (sum(1 / (1 + max(0, x["vs_lowest_pct"] or 0) / 100) for x in answered) / max(1, len(answered)))
        proc = 0.4 * max(0, ans - 0.25 * chased) + 0.3 * comp + 0.3 * price; conf_p = min(1.0, n / 2)
        stats = {**(stats or {}), "rfqs": n, "answer_rate": ans, "chased_rate": chased, "complete": comp, "price_discipline": price}
        ev.append(f"answered {len(answered)} of {n} RFQ(s), needed a chaser {chased:.0%} of the time, quotes {comp:.0%} complete, price discipline {price:.0%}")
    else:
        proc, conf_p = 0.5, 0.0
        ev.append("no RFQ behaviour recorded")
    pts = w * (2 / 3 * _blend(job, conf_j) + 1 / 3 * _blend(proc, conf_p))
    thin = (conf_j + conf_p) < 1.0
    txt = "; ".join(ev) + ("; thin evidence, score pulled toward neutral" if thin and (hist or sigs) else "") + ("; UNVERIFIED vendor, neutral half-score" if not hist and not sigs else "")
    return pts, txt, stats


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
        hist = history(key) if key else []; sigs = signals(key) if key else []
        rows.append({"q": q, "n": nq, "vendor_row": dict(v) if v else None, "registered": bool(v and v["registered"]), "hist": hist, "sigs": sigs})
    lo = min(r["n"]["landed"] for r in rows)
    for r in rows:
        n = r["n"]
        p = w["price"] * lo / n["landed"]
        pf, pf_txt, pf_stats = perf_score(r["hist"], r["sigs"], w["performance"])
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
    syn = sum(1 for x in r["hist"] + r["sigs"] if "SYNTHETIC" in (x.get("source") or "")); real = len(r["hist"]) + len(r["sigs"]) - syn
    out.append("Track record: " + r["perf_txt"] + (f" [{syn} synthetic demo record(s), {real} observed by the agent]" if syn else (f" [{real} record(s) observed by the agent]" if real else "")) + ".")
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
