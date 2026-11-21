"""Step 4b: turn one extracted quote into a like-for-like landed cost at the TENDER quantities.
Pure code: no LLM. Every assumption is recorded as a flag so the HOD can see it."""
from datetime import date
from .tender import TENDER, SQM_PER_SQFT

# ASSUMPTIONS (editable, shown in the UI)
ASSUMP = {"gst_default_pct": 18.0, "cost_of_capital_pct": 12.0, "project_months": 19, "baseline_credit_days": 30,
          "today": date(2026, 11, 21), "stale_days": 90}

_UNIT = {"SQFT": "SQFT", "SFT": "SQFT", "SQ.FT": "SQFT", "SQM": "SQM", "SQM.": "SQM", "SQMT": "SQM", "M2": "SQM",
         "RMT": "RMT", "RM": "RMT", "RMTR": "RMT", "RMT.": "RMT", "MTR": "RMT"}
_AREA = {"SQFT", "SQM"}


def canon_unit(u):
    return _UNIT.get((u or "").strip().upper().replace(" ", ""), (u or "").strip().upper())


def rate_in_tender_unit(rate, vunit, tunit):
    """Return (rate per tender unit, flag|None)."""
    v, t = canon_unit(vunit), canon_unit(tunit)
    if v == t:
        return rate, None
    if v == "SQM" and t == "SQFT":
        return rate * SQM_PER_SQFT, f"converted {rate:g}/SQM to {rate * SQM_PER_SQFT:.2f}/SQFT"
    if v == "SQFT" and t == "SQM":
        return rate / SQM_PER_SQFT, f"converted {rate:g}/SQFT to {rate / SQM_PER_SQFT:.2f}/SQM"
    # area vs running-metre cannot be converted without a width: assume the vendor mistyped the unit
    return rate, f"vendor unit {v} differs from tender unit {t}; cannot convert, assumed typo and used the rate as quoted. Confirm with vendor"


def normalise(q: dict, history: dict | None = None) -> dict:
    A = ASSUMP
    flags, lines = [], []
    by_sl = {l["sl"]: l for l in q.get("lines", [])}
    base = 0.0
    for tl in TENDER["lines"]:
        vl = by_sl.get(tl["sl"])
        if not vl or vl.get("rate") is None:
            lines.append({"sl": tl["sl"], "rate": None, "cost": None, "flags": ["no rate quoted"]}); flags.append(f"item {tl['sl']}: no rate")
            continue
        r, f = rate_in_tender_unit(vl["rate"], vl.get("unit"), tl["unit"])
        lf = [f] if f else []
        vq = vl.get("quantity")
        if vq and abs(vq - tl["qty"]) / tl["qty"] > 0.01:
            lf.append(f"vendor priced {vq:,.0f} {canon_unit(vl.get('unit'))}, tender is {tl['qty']:,.2f} {tl['unit']}; re-priced at tender quantity")
        if vl.get("rate_confidence") in ("low", "medium"):
            lf.append(f"rate read with {vl['rate_confidence']} confidence" + (f": {vl['note']}" if vl.get("note") else ""))
        cost = r * tl["qty"]; base += cost
        lines.append({"sl": tl["sl"], "rate": r, "rate_quoted": vl["rate"], "unit_quoted": canon_unit(vl.get("unit")), "cost": cost, "flags": lf,
                      "confidence": vl.get("rate_confidence", "high")})
    qty_mismatch = any(any("re-priced" in x for x in l["flags"]) for l in lines)
    if qty_mismatch:
        flags.append("Vendor priced different quantities from the tender; all totals below are re-priced at the tender quantities")

    # GST
    g = q.get("gst") or {}
    gst_pct = g.get("rate_pct")
    gst_assumed = gst_pct is None
    if gst_assumed:
        gst_pct = A["gst_default_pct"]
        flags.append(f"GST rate not stated in the quote ({g.get('basis', 'unstated')}); assumed {gst_pct:g}% extra, the standard works-contract rate. Confirm")
    gst = base * gst_pct / 100

    # payment terms -> money
    pt = q.get("payment_terms") or {}
    adv, cd = pt.get("advance_pct"), pt.get("credit_days")
    terms_stated = adv is not None or cd is not None
    if not terms_stated:
        flags.append("Payment terms not stated; baseline assumed (no advance, 30 days). Confirm")
    adv = adv or 0.0
    cd = A["baseline_credit_days"] if cd is None else cd
    coc = A["cost_of_capital_pct"] / 100
    gross = base + gst
    # advance sits with the vendor for about half the project on average
    adv_cost = gross * adv / 100 * coc * (A["project_months"] / 12) / 2
    # paying faster than the 30-day baseline costs us the float
    float_cost = gross * coc * max(0, A["baseline_credit_days"] - cd) / 365
    landed = gross + adv_cost + float_cost

    # freshness
    qd = q.get("quote_date")
    age = (A["today"] - date.fromisoformat(qd)).days if qd else None
    validity = q.get("validity_days")
    expired = bool(age is not None and validity and age > validity)
    if expired:
        flags.append(f"Quote is {age} days old and its validity was {validity} days: expired, needs re-confirmation")
    elif age is not None and age > A["stale_days"]:
        flags.append(f"Quote is {age} days old with no stated validity: stale, needs re-confirmation")
    elif age is None:
        flags.append("Quote date not found")
    low_conf = sum(1 for l in lines if l.get("confidence") in ("low", "medium"))
    return {
        "vendor": q.get("vendor_name"), "lines": lines, "base_ex_gst": base, "gst_pct": gst_pct, "gst_assumed": gst_assumed, "gst": gst,
        "advance_pct": adv, "credit_days": cd, "terms_stated": terms_stated, "advance_cost": adv_cost, "float_cost": float_cost,
        "landed": landed, "quote_age_days": age, "validity_days": validity, "expired": expired, "stale": expired or (age or 0) > A["stale_days"] and not validity,
        "warranty_years": q.get("warranty_years"), "client_supplies": q.get("client_supplies", []), "low_conf_lines": low_conf,
        "qty_mismatch": qty_mismatch, "flags": flags, "other_terms": q.get("other_terms", []),
    }
