from .tender import TENDER


def draft(vendor_name: str) -> tuple[str, str]:
    t = TENDER
    lines = "\n".join(f"  {l['sl']}. {l['title']}: {l['qty']:,.2f} {l['unit']}   (ERP code {l['item_code']})" for l in t["lines"])
    terms = "\n".join(f"  {i}. {x}" for i, x in enumerate(t["standard_terms"], 1))
    subject = f"SEEKING QUOTATION FOR WATERPROOFING WORK AT {t['project'].upper()} ({t['package']})"
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
