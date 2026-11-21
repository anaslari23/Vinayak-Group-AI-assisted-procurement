# Vinayak procurement agent

**A procurement agent that lives in chat. A site engineer sends a requirement as text or a voice note; the agent asks what is missing, matches the item code, sends RFQs, reads vendor replies in any format, chases and clarifies, ranks vendors on price and a real track record with plain-words reasons, and brings the HOD one message to decide. It recommends; it never selects, orders or pays.**

Built for the Vinayak Group problem statement "AI-assisted procurement: from site requirement to vendor comparative", Vibe Coders Hackathon Edition 3 (Calcutta AI Club, 21 Nov 2026).

- **Deployed link:** _to be added_
- **Demo video (2 min):** _to be added_
- **Eval:** item-code matching 36 / 36 statuses right, 0 confidently wrong, on 36 requests (the 14 from the Step 2 spec plus our own; written by us, so a small friendly set). Quote extraction 360 / 360 fields correct across 12 quotes in 4 layouts (clean table, letterhead, email-style, handwritten scan), and 12 / 12 landed-cost totals matching a hand calculation. See [limits](#known-limitations).

## Why an agent, not a form

Today every step is a manual handoff: engineer to HOD, item code, RFQ emails, quote collection, comparative, selection. This agent removes the handoffs. The human is asked only for decisions the agent cannot make:

| Step | Who does it here |
|---|---|
| Capture requirement (text, WhatsApp-style message, voice note) | Agent. Asks the engineer a follow-up in the same chat if something is missing. |
| Item code | Agent runs the Step 2 matcher (below). It proceeds alone only on an exact, conflict-free match, logs it, and otherwise asks: the engineer for missing detail, or the HOD with the top 3 and one tap. |
| RFQs | Agent picks vendors by category and sends (test inboxes only). |
| Collecting quotes | Agent watches the inbox, reads each reply, asks vendors for missing GST, payment terms, unclear rates or unit mismatches, and chases silent vendors after 24 h. |
| Comparative | Agent re-prices every quote at the tender quantities on a like-for-like landed cost. |
| Ranking | Agent scores price + vendor track record + terms and risk, with reasons for every vendor. |
| Final call | **HOD**, with one chat message and Approve / Send back / Reject buttons. |
| Vendor record | Agent builds it: it observes behaviour in every RFQ round, then asks the site engineer three one-tap questions after the work (on time? quality? rework or dispute?). |

## The calling agent (voice, any Indian language)

A site engineer can **speak** a requirement in Hindi, Bengali, Tamil, Telugu, Kannada, Malayalam, Marathi, Gujarati, Punjabi, Odia, English or Hinglish. Sarvam speech-to-text detects the language; the agent understands it, asks what is missing, **reads the requirement back and waits for a yes** (the engineer confirms before anything proceeds), and answers in the caller's language with Sarvam text-to-speech. Corrections ("no, it is for the floor") are merged and read back again.

- **Browser call page** at `/call` (tap to speak; works on localhost or https). Latency is about 6 to 12 seconds a turn because each turn is speech-to-text, a model call and speech-to-text back; fine for a demo, slow for a real call.
- **Telegram:** send a voice note; the bot replies with text and a voice note in your language.
- **A real phone number:** `/telephony/voice` and `/telephony/recording` are a Twilio-style webhook (Record, then Play the reply) onto the same agent. **It is untested**: it needs a telephony provider account and number (Indian numbers need KYC), plus a public URL. Until then the browser page and Telegram carry the voice flow.

## Where it lives

- **Telegram bot** (live): one bot, two roles. Everyone is a site engineer by default; `/hod` registers a chat as the HOD. Text and voice notes (Sarvam speech-to-text). Buttons are inline.
- **Two-phone simulator** at `/sim`: the same conversation as seen from the engineer's and the HOD's phones, with a case timeline at `/cases`, so it can be watched without installing anything.
- WhatsApp needs Meta business approval, so it is not used; the channel is an adapter (`app/telegram_bot.py`) and could be swapped.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add OPENROUTER_API_KEY, SARVAM_API_KEY, TELEGRAM_BOT_TOKEN
.venv/bin/uvicorn app.main:app --port 8000
```

Open http://localhost:8000/sim. Run [Mailpit](https://mailpit.axllent.org) (`mailpit`, inbox UI at :8025) to see real RFQ, follow-up and vendor-reply emails; without it the app falls back to injecting replies directly. Only one process may poll a Telegram bot at a time, so set `TELEGRAM_BOT_TOKEN` on one host only.

## Item-code matching (Step 2, built to the Step 2 spec)

`app/itemmatch.py`, deterministic and explainable. It cleans the ERP list at load (typos, stars, brackets, long specifications cut to a title), groups it, and flags the mess a real export has: repeated rows, one code with two meanings, one work under many codes, look-alike codes, a site-sheet code that means something else in the ERP. A request is cleaned (Hinglish and site words via an editable synonym table, typos), split into parts (area, surface, side, height cues) and every ERP group is scored on word overlap, fuzzy spelling and part agreement. Result: top 3 with confidence and a reason, one of five statuses (`matched_high`, `needs_back_office_confirm`, `needs_more_info`, `suggest_new_code`, `conflict_in_erp`), flags, and either a question back or a draft new code (never created, numbering follows the list's own pattern).

- **Auto on exact:** the agent proceeds without a person only when the code is exact and conflict-free (for example a site-sheet code that agrees with the ERP). Everything else goes to a person. Floor versus wall can never swap.
- **Memory and audit:** the back office's picks are saved and used first next time; every run is logged append-only (who asked, ERP load date, candidates, flags, who approved which code). `/erp-cleanup` lists every duplicate and conflict sorted by how often requests hit it, with a CSV, and has the synonym editor.
- **Never writes to Farvision.** It reads an exported list and recommends.
- **Out of category** (road, steel, cement): says "no code list loaded for this category" and does not invent a code. Adding a category means dropping in one more list.

## How the agent works

- **Case manager** (`app/engine.py`): one case per requisition, a deterministic state machine, and an event log of everything the agent does (shown as the timeline). Claude is used for judgment (parse a message, read a quote, merge a vendor's reply, write the HOD note); control flow is plain code, so behaviour is predictable.
- **Quote reading**: Claude reads PDFs, scans and images; code then normalises units (SQM vs SQFT), GST inclusive vs extra, freight, advance and credit days, quote validity, and re-prices at the **tender** quantities (vendors often quote different quantities). Every interpretation is flagged.
- **Ranking** (`app/scoring.py`): pure arithmetic, no AI. Price 50 + vendor performance 30 + terms and risk 20 (weights adjustable on the compare page). Reasons are generated from the numbers, and the AI-written HOD summary is checked so it cannot cite a number that is not in the data.
- **Vendor ledger** (`/vendors`): job record (quality, delivery, disputes) with recent jobs weighted more, plus observed buying-process behaviour (answer rate, chasers, quote completeness, price discipline). Thin evidence pulls toward a neutral score and says so. One bad recorded job visibly changes the next recommendation.
- **Guardrails**: every outgoing email is checked against a recipient allowlist before sending, so only test inboxes can be reached; there is no order, award or payment code path; the agent labels what it assumed.

## Real vs mocked

| Real | Mocked / assumed |
|---|---|
| Telegram bot, Sarvam speech-to-text, LLM quote reading, normalisation, scoring, item matching, email send and inbox watching (Mailpit), chasers, follow-ups, post-job check-ins, signed HOD links | **All data in this repo is synthetic** (project, vendors, rates, item master, vendor history). No Vinayak data is included. |
| | Vendors are simulated by buttons on the case page ("Vendors email their quotes", "Vendors answer follow-ups", "Fast-forward"), which send real emails into the test inbox. |
| | The demo vendor history is invented to show the mechanism. Vinayak records none today. Vendors with no record get a neutral half-score and are marked unverified. |
| | New item-code numbering and the Excel column names are **ASSUMED**; Farvision's import format is not public. No live Farvision calls. |
| | The four headline quotes are served from cached extractions (fixture mode) so the demo is instant; uploading a new quote runs the live extractor. |
| | On a hosted instance with no Mailpit, vendor replies are injected directly and emails show as "logged only". |

**Positioning:** [Farvision](https://farvisionerp.com/farvision-module-materials-p2p) already describes RFQ, a weighted comparative and L1 recommendation. This agent is the layer in front of it: it turns unstructured quotes into comparable data, runs the chasing, and builds the vendor history. It does not replace Farvision.

## Assumptions you can change

`app/normalise.py`: GST 18% when unstated (works-contract rate), 12% cost of capital, advance outstanding for about half of a 19-month project, 30-day baseline credit, quotes older than 90 days with no validity treated as stale. `app/engine.py`: 24 h chaser, 48 h deadline, 60-day work period before the check-in.

## Data and tests

- `data/demo/`: synthetic tender, item master, vendor list, synthetic history and RFQ behaviour, 12 generated quote files with ground truth. `python scripts/make_demo_data.py` regenerates them and `python scripts/extract_demo.py` re-runs the extractor.
- `python -m pytest tests` covers the allowlist, unit conversion, re-pricing, the full agent flow (clarify, RFQ, quotes, chasers, HOD, check-in), ledger recency and evidence weighting, and Telegram routing.
- Local use with real documents: `data/private/` is gitignored and `DATA_DIR=data/private` points the app at it.

## AI tools used

Claude (via OpenRouter) for quote reading, requirement structuring, merging vendor replies and the HOD note; Sarvam for speech-to-text; Claude Code for writing the code.

## Known limitations

- The phone-number webhook is untested without a provider account. Voice quality and language detection are as good as Sarvam's; heavy code-mixing or noisy sites will cause misreadings, which the read-back and confirm step is there to catch.

- Item matching is scored on a synthetic item list that carries the same kinds of mess as a real export; the cases were written by us. It covers one category (waterproofing); other categories need their own list, and Bengali needs a larger word table.
- Open-request duplicates are checked on project, wording and quantity within 7 days (the test of the spec flag list is covered). A message with several items is split by the matcher, but the agent flow processes one item (or one BOQ package) per case.

- The agent reads quotes and compares only for packages where it holds a BOQ. For a single-item requirement without a BOQ (for example the road-work demo) it stops after sending RFQs; generic quote reading is not built.
- The eval set is small and generated, with clean layouts and one handwriting style. Real quotes will be messier; low-confidence reads are flagged and sent back to the vendor as questions instead of being trusted.
- Client-supplied materials (cement, sand, scaffolding) are listed per vendor but not priced.
- The cost-of-capital model for advances is a simple stated assumption.
- Vendor categories for the contractor list are inferred, not recorded.
- Time is simulated in the demo (fast-forward buttons); production would use real timers.
- Single-user demo: no login, SQLite, state resets on redeploy.
