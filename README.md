# Vinayak procurement agent

**An AI agent that turns messy vendor quotes into a like-for-like comparative statement, ranks vendors with plain-words reasons, and asks the HOD to decide. It recommends; it never selects, orders or pays.**

Built for the Vinayak Group problem statement "AI-assisted procurement: from site requirement to vendor comparative", Vibe Coders Hackathon Edition 3 (Calcutta AI Club, 21 Nov 2026).

- **Deployed link:** _to be added_
- **Demo video (2 min):** _to be added_
- **Eval:** 100% field-level extraction accuracy (360 / 360 fields, 12 quotes, 4 layouts) and 12 / 12 landed-cost totals matching a hand calculation. See [limits](#known-limitations).

## Run locally (3 commands)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add OPENROUTER_API_KEY (and SARVAM_API_KEY for voice notes)
.venv/bin/uvicorn app.main:app --port 8000
```

Optional: run [Mailpit](https://mailpit.axllent.org) (`mailpit`) so RFQ and HOD mails land in a visible test inbox at http://localhost:8025. Without it, mails are logged inside the app and marked "logged only".

## What it does (the six steps)

| Step | What happens | Where |
|---|---|---|
| 1 Capture | Text/WhatsApp-style message or a voice note (Sarvam speech-to-text). Claude structures it and lists what the HOD would have to ask back. | `/requirement` |
| 2 Item code | Attribute match, then fuzzy match (rapidfuzz) against the ERP item list. Flags repeated codes and near-duplicates; suggests a new code when nothing fits. | `/requirement` |
| 3 RFQ | Draft per vendor, sent to a **test inbox only**. A hard allowlist in code blocks every other address. | `/rfq` |
| 4 Read quotes | Claude reads PDF, scan, image or Excel quotes. Code then re-prices everything at the **tender quantities and units**: unit conversion (SQM vs SQFT), GST inclusive vs extra, freight, advance and credit days, quote validity. Every assumption is flagged. | `/quotes` |
| 5 Compare | Landed cost + weighted score (price, vendor history, terms and risk). Pure arithmetic, no AI in the ranking. Reasons per vendor, weights adjustable, "rank if history ignored" shown, Excel export. | `/compare` |
| 6 HOD | Recommendation email with reasons and a signed link to a confirmation page. The HOD's decision is recorded as a note. | `/hod` |

**Guardrails:** recipient allowlist enforced before any send; no order, award or payment code path exists; every ranking shows its reasons; the AI summary is checked so it cannot cite a number that is not in the computed data (otherwise a template is used).

## Real vs mocked

| Real | Mocked / assumed |
|---|---|
| LLM reading of PDFs and scans; unit, GST, freight and payment-term normalisation; scoring; item matching; signed HOD links; SMTP send to Mailpit; Sarvam speech-to-text | **All data in this repo is synthetic** (project, vendors, rates, item master, vendor history). No Vinayak data is included. |
| | Vendor past performance does not exist anywhere today; the demo history is synthetic, and there is a mechanism to capture and score it. Vendors with no record get a neutral half-score and are marked unverified. |
| | Item-code numbering for new codes and the Excel column names are **ASSUMED**; Farvision's import format is not public. We make no live Farvision calls. |
| | On the deployed link there is no SMTP server, so mails are shown in the in-app log ("logged only"). |
| | The four headline quotes are served from cached extractions (fixture mode) so the demo is instant and survives restarts; uploading a new quote runs the live extractor. |

**Positioning:** [Farvision](https://farvisionerp.com/farvision-module-materials-p2p) already describes RFQ, a weighted comparative and L1 recommendation. This agent is the layer in front of it: it turns unstructured quotes into structured, comparable data and brings in vendor history. It does not replace Farvision.

## Assumptions you can change

`app/normalise.py`: GST 18% when unstated (works-contract rate), 12% cost of capital, advance outstanding for about half of a 19-month project, 30-day baseline credit, quotes older than 90 days with no validity treated as stale. Scoring weights (price 50, history 30, terms 20) are adjustable on the compare page.

## Data and tests

- `data/demo/`: synthetic tender, item master, vendor list, 12 generated quote files and their ground truth (`python scripts/make_demo_data.py` regenerates them; `python scripts/extract_demo.py` re-runs the extractor).
- `python -m pytest tests` covers the allowlist, unit conversion, re-pricing and GST handling.
- Local use with real documents: `data/private/` is gitignored and `DATA_DIR=data/private` points the app at it.

## AI tools used

Claude (via OpenRouter) for quote reading, requirement structuring and the HOD summary; Sarvam for speech-to-text; Claude Code for writing the code.

## Known limitations

- The eval set is small, generated, and uses clean layouts and one handwriting style; real quotes will be messier. Low-confidence reads are flagged rather than trusted.
- Client-supplied materials (cement, sand, scaffolding) are listed per vendor but not priced.
- The cost-of-capital model for advances is a simple stated assumption.
- Vendor categories for the contractor list are inferred, not recorded.
- Single-user demo: no login, SQLite, state resets on redeploy.
