import pytest
from app import mailer
from app.normalise import rate_in_tender_unit, canon_unit, normalise


def test_allowlist_blocks_real_and_tricky_addresses():
    for bad in ("v@realvendor.com", "a@rfq-test.example,b@gmail.com", "x@gmail.com;y@rfq-test.example", "rfq-test.example"):
        with pytest.raises(mailer.RecipientBlocked):
            mailer.check(bad)
    mailer.check("rfq+purvyakruti@rfq-test.example")


def test_unit_conversion():
    r, f = rate_in_tender_unit(34.5, "SQM.", "SQFT")
    assert abs(r - 34.5 * 0.09290304) < 1e-9 and "converted" in f
    r, f = rate_in_tender_unit(34.5, "SQM.", "RMT")  # area vs length: cannot convert, flagged
    assert r == 34.5 and "assumed typo" in f
    assert canon_unit("SFT") == "SQFT"


def test_repriced_at_tender_qty_and_gst_assumed():
    q = {"vendor_name": "X", "gst": {"rate_pct": None, "basis": "unstated"},
         "lines": [{"sl": i, "unit": "SQFT" if i != 3 else "RMT", "quantity": 99999, "rate": 10, "rate_confidence": "high"} for i in range(1, 8)]}
    n = normalise(q)
    assert n["qty_mismatch"] and n["gst_assumed"]
    from app.tender import TENDER
    assert abs(n["base_ex_gst"] - 10 * sum(l["qty"] for l in TENDER["lines"])) < 0.01
