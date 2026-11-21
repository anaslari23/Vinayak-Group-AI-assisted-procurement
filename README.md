# Vinayak procurement agent

AI-assisted procurement for Vinayak Group: from a site requirement to a vendor comparative statement and an HOD recommendation.

Built at Vibe Coders Hackathon Edition 3 (Calcutta AI Club, 21 Nov 2026, Kolkata).

- **Deployed link:** _coming soon_
- **Demo video:** _coming soon_

## Problem

Material requirements and work orders go through a manual chain: site engineer -> HOD -> item code in ERP -> RFQ emails -> manual quote collection -> comparative statement -> vendor selection. Quotes arrive as PDFs and emails with inconsistent units, taxes and terms, and vendor past performance is not recorded anywhere.

## What it does (planned)

1. Capture a site requirement in a structured form.
2. Match it to an existing item code, or suggest a new one, and flag likely duplicates.
3. Draft RFQs for the right vendors (test inbox only).
4. Read vendor quotes in any format and normalise unit rate, GST, freight, payment terms and delivery time.
5. Rank vendors on landed price and past performance, with a plain-words reason for every rank.
6. Send the recommendation to the HOD for approval.

## Guardrails

- RFQs only go to a test inbox, enforced by a recipient allowlist in code. No real vendor is ever emailed.
- The agent only recommends. There is no order or payment code path. The HOD decides.

## Run locally

_To be added once the app lands._

## Real vs mocked

_To be filled in honestly as features are built._

## AI tools used

Claude (Claude Code) for coding assistance.

## Known limitations

Work in progress. The hackathon data pack is confidential and is not included in this repo.
