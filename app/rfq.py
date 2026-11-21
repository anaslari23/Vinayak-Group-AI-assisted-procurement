from .tender import TENDER


def draft(vendor_name: str, case_id: int | None = None) -> tuple[str, str]:
    t = TENDER
    lines = "\n".join(f"  {l['sl']}. {l['title']}: {l['qty']:,.2f} {l['unit']}   (ERP code {l['item_code']})" for l in t["lines"])
    terms = "\n".join(f"  {i}. {x}" for i, x in enumerate(t["standard_terms"], 1))
    subject = f"SEEKING QUOTATION FOR WATERPROOFING WORK AT {t['project'].upper()} ({t['package']})" + (f" [Case #{case_id}]" if case_id else "")
    body = f"""Dear {vendor_name},

Please find the description of work and quantities below. Kindly submit your commercial quotation against these quantities and the standard terms.

Quantities (please quote against these exact quantities and units):
{lines}

Vinayak standard terms:
{terms}

To make comparison fair, please state clearly in your reply:
  - unit rate for each item in the unit shown above (SQFT / RMT)
  - GST rate and whether rates are inclusive or exclusive
  - freight / mobilisation, if any
  - payment terms (advance %, credit days)
  - start time after order and total duration
  - quote validity and warranty

Thanks & Regards,
Contracts & Planning, Vinayak Group
(TEST RFQ: sent to a test inbox, not to a real vendor)
"""
    return subject, body


def draft_generic(vendor_name: str, parsed: dict, item_code: str | None, case_id: int | None = None) -> tuple[str, str]:
    """RFQ for a requirement that has no BOQ package on file (single item)."""
    subject = f"SEEKING QUOTATION: {parsed.get('item_description', 'material')}" + (f" [Case #{case_id}]" if case_id else "")
    body = f"""Dear {vendor_name},

Please quote for the following requirement.

  Item: {parsed.get('item_description')}
  ERP item code: {item_code or 'to be created'}
  Quantity: {parsed.get('quantity') or 'to be confirmed'} {parsed.get('uom') or ''}
  Project / location: {parsed.get('project') or parsed.get('location') or '-'}
  Needed by: {parsed.get('needed_by') or '-'}

Please state unit rate, GST rate and whether rates are inclusive or exclusive, freight, payment terms, delivery time and quote validity.

Thanks & Regards,
Contracts & Planning
(TEST RFQ: sent to a test inbox, not to a real vendor)
"""
    return subject, body
