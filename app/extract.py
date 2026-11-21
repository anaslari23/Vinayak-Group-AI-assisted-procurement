"""Step 4a: read a vendor quote (PDF/scan/image) into structured JSON. LLM reads; code validates."""
from pathlib import Path
from .llm import ask_json

PROMPT = """You are reading a vendor quotation for a construction work package. Extract ONLY what is printed/written.
Return one JSON object, no prose:
{
 "vendor_name": str, "quote_date": "YYYY-MM-DD" or null, "validity_days": int|null,
 "gstin": str|null,
 "gst": {"rate_pct": number|null, "basis": "extra"|"inclusive"|"unstated"},
 "freight": {"included": bool|null, "note": str|null},
 "payment_terms": {"advance_pct": number|null, "credit_days": int|null, "text": str|null},
 "start_days_after_order": int|null,
 "warranty_years": number|null,
 "client_supplies": [str],   // things the client must provide free (cement, sand, scaffolding, power, hutment...)
 "other_terms": [str],       // short plain-words bullets of anything else commercially important
 "lines": [{"sl": int, "title": str, "unit": str, "quantity": number|null, "rate": number|null,
            "rate_confidence": "high"|"medium"|"low", "note": str|null}]
}
Rules: sl is the serial number of the item row. title is the bold item heading only (not the long method text).
rate is the per-unit rate (not the amount). Keep the unit exactly as printed (SQFT, SQM, RMT...).
If handwriting or a scan makes a rate ambiguous, give your best reading, set rate_confidence low and explain in note.
Never invent a value; use null when absent."""


def extract_quote(path: Path) -> dict:
    return ask_json(PROMPT, [Path(path)])
