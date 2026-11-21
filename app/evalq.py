"""Eval: field-level accuracy of quote extraction vs ground truth. For the synthetic set the truth is known by construction (the quote files are generated from it)."""
import json
from .normalise import canon_unit, normalise
from .tender import DATA_DIR


def run(quotes):
    TRUTH = json.loads((DATA_DIR / "truth.json").read_text())
    tot = ok = 0; misses = []; base_ok = base_n = 0
    for q in quotes:
        key = next((k for k in TRUTH if k.upper() in q["vendor_name"].upper()), None)
        if not key: continue
        t = TRUTH[key]; pt = q.get("payment_terms") or {}; g = q.get("gst") or {}
        checks = [("quote date", q.get("quote_date"), t["date"]), ("validity", q.get("validity_days"), t["validity"]),
                  ("GST rate", g.get("rate_pct"), t["gst"][0]), ("GST basis", g.get("basis"), t["gst"][1]),
                  ("advance %", pt.get("advance_pct"), t["adv"]), ("credit days", pt.get("credit_days"), t["credit"]),
                  ("warranty", q.get("warranty_years"), t["warranty"]), ("start days", q.get("start_days_after_order"), t["start"]),
                  ("freight amount", (q.get("freight") or {}).get("amount"), t.get("freight"))]
        by = {l["sl"]: l for l in q["lines"]}
        for i in range(7):
            l = by.get(i + 1, {})
            checks += [(f"item {i+1} unit", canon_unit(l.get("unit")), t["units"][i]), (f"item {i+1} qty", l.get("quantity"), t["qty"][i]), (f"item {i+1} rate", l.get("rate"), t["rate"][i])]
        for name, got, exp in checks:
            tot += 1
            good = (got == exp) or (isinstance(got, (int, float)) and isinstance(exp, (int, float)) and abs(got - exp) < 0.005)
            ok += good
            if not good: misses.append(f"{key}: {name}: got {got!r}, expected {exp!r}")
        base_n += 1; b = normalise(q)["base_ex_gst"]; base_ok += abs(b - t["base"]) <= 2
    return {"fields": tot, "correct": ok, "acc": ok / tot if tot else 0, "misses": misses, "base_ok": base_ok, "base_n": base_n, "n_quotes": base_n}
