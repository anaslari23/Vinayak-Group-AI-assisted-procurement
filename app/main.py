import io, json, os, tempfile
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import openpyxl
from . import db, items, rfq, mailer, recommend
from .scoring import rank, reasons, W
from .normalise import ASSUMP
from .tender import TENDER
from .extract import extract_quote
from .parse_req import parse, transcribe

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Vinayak procurement agent")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
T = Jinja2Templates(directory=ROOT / "templates")
T.env.filters["inr"] = lambda v: f"{v:,.0f}" if v is not None else "-"
T.env.filters["inr2"] = lambda v: f"{v:,.2f}" if v is not None else "-"
from .tender import DATA_DIR
CACHE = DATA_DIR / "cache"
STEPS = [("A", "Agent", "/cases"), ("P", "Phones", "/sim"), ("V", "Vendors", "/vendors"), ("1", "Capture", "/requirement"), ("2", "Item code", "/requirement"), ("3", "RFQ", "/rfq"), ("4", "Quotes", "/quotes"), ("5", "Compare", "/compare"), ("6", "HOD", "/hod")]


@app.on_event("startup")
def boot():
    db.init()
    from . import telegram_bot
    print("[agent]", telegram_bot.start())
    from . import scheduler
    scheduler.start()
    c = db.con()
    if c.execute("select count(*) from quotes").fetchone()[0] == 0 and CACHE.exists():  # cached extractions of the 4 received quotes (see README)
        for f in sorted(CACHE.glob("*.json")):
            q = json.loads(f.read_text()); v, _ = db.match_vendor(q["vendor_name"])
            c.execute("insert or replace into quotes(case_id,vendor_key,source_file,extracted) values(0,?,?,?)", ((v["key"] if v else q["vendor_name"]), f.name, json.dumps(q)))
        c.commit()
    c.close()


def page(request, name, active, **ctx):
    return T.TemplateResponse(request, name, {"steps": STEPS, "active": active, **ctx})


def all_quotes(case_id=0):
    c = db.con(); r = [json.loads(x["extracted"]) for x in c.execute("select extracted from quotes where case_id=?", (case_id,))]; c.close(); return r


def weights(request):
    try:
        w = {k: float(request.query_params.get(k, W[k])) for k in W}
    except ValueError:
        w = dict(W)
    s = sum(w.values()) or 1
    return {k: v * 100 / s for k, v in w.items()}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    c = db.con(); n = {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in ("requirements", "quotes", "mail_log", "decisions")}; c.close()
    return page(request, "home.html", "", n=n, tender=TENDER)


# ---- steps 1+2
@app.get("/requirement", response_class=HTMLResponse)
def req_form(request: Request):
    c = db.con(); recent = [dict(r) for r in c.execute("select * from requirements order by id desc limit 5")]; c.close()
    for r in recent: r["parsed"] = json.loads(r["parsed"]); r["match"] = json.loads(r["match"])
    return page(request, "requirement.html", "1", recent=recent, result=None)


@app.post("/requirement", response_class=HTMLResponse)
async def req_submit(request: Request, text: str = Form(""), audio: UploadFile | None = File(None)):
    channel, err = "form/text", None
    if audio and audio.filename:
        try:
            with tempfile.NamedTemporaryFile(suffix=Path(audio.filename).suffix, delete=False) as f:
                f.write(await audio.read())
            text, channel = transcribe(Path(f.name)), "voice note (Sarvam STT)"
        except Exception as e:
            err = f"Voice transcription failed: {e}"
    result = None
    if not text.strip() and not err:
        err = "Type or paste a requirement, or upload a voice note."
    if text.strip():
        parsed = parse(text)
        m = items.match(parsed.get("item_description") or text)
        c = db.con(); c.execute("insert into requirements(raw,channel,parsed,match) values(?,?,?,?)", (text, channel, json.dumps(parsed), json.dumps(m))); c.commit(); c.close()
        result = {"raw": text, "channel": channel, "parsed": parsed, "match": m}
    return page(request, "requirement.html", "1", recent=[], result=result, err=err)


# ---- step 3
@app.get("/rfq", response_class=HTMLResponse)
def rfq_page(request: Request):
    c = db.con(); v = [dict(r) for r in c.execute("select * from vendors where category='waterproofing' order by registered desc, name")]
    log = [dict(r) for r in c.execute("select * from mail_log where kind='rfq' order by id desc limit 10")]; c.close()
    subj, body = rfq.draft("{vendor name}")
    return page(request, "rfq.html", "3", vendors=v, subject=subj, body=body, log=log, allowed=mailer.ALLOWED_DOMAINS)


@app.post("/rfq/send")
def rfq_send(vendors: list[str] = Form([])):
    c = db.con()
    for k in vendors:
        v = c.execute("select * from vendors where key=?", (k,)).fetchone()
        if v:
            subj, body = rfq.draft(v["name"])
            try:
                mailer.send("rfq", v["test_email"], subj, body)
            except mailer.RecipientBlocked:
                pass
    c.close()
    return RedirectResponse("/rfq", 303)


# ---- step 4
@app.get("/quotes", response_class=HTMLResponse)
def quotes_page(request: Request):
    rows = rank(all_quotes(), weights(request))
    return page(request, "quotes.html", "4", rows=sorted(rows, key=lambda r: r["n"]["vendor"]), tender=TENDER)


@app.post("/quotes/upload")
async def quote_upload(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(await file.read())
    path = Path(f.name)
    if suffix in (".xlsx", ".xls"):  # Claude document input does not take Excel: convert to a text-like PDF-free representation
        wb = openpyxl.load_workbook(path, data_only=True)
        txt = "\n".join("\t".join("" if c is None else str(c) for c in r) for ws in wb for r in ws.iter_rows(values_only=True))
        path = path.with_suffix(".txt"); path.write_text(txt)
    q = extract_quote(path)
    v, _ = db.match_vendor(q["vendor_name"])
    c = db.con(); c.execute("insert or replace into quotes(case_id,vendor_key,source_file,extracted) values(0,?,?,?)", ((v["key"] if v else q["vendor_name"]), file.filename, json.dumps(q))); c.commit(); c.close()
    return RedirectResponse("/quotes", 303)


# ---- step 5
@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request):
    w = weights(request)
    rows = rank(all_quotes(), w)
    for r in rows: r["reasons"] = reasons(r, rows)
    nohist = {r["n"]["vendor"]: r["rank"] for r in rank(all_quotes(), {**w, "performance": 0.0001})}
    for r in rows: r["rank_no_hist"] = nohist[r["n"]["vendor"]]
    summ, how = recommend.summary(rows)
    return page(request, "compare.html", "5", rows=rows, w=w, tender=TENDER, assump=ASSUMP, summ=summ, how=how)


@app.get("/compare.xlsx")
def compare_xlsx(request: Request):
    rows = rank(all_quotes(), weights(request))
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Comparative Statement"
    ws.append(["ASSUMED Farvision-style layout. Column names are assumptions, not a documented Farvision import format."])
    ws.append(["SL", "ITEM CODE", "DESCRIPTION", "UOM", "TENDER QTY"] + [r["n"]["vendor"] + " rate" for r in rows])
    for tl in TENDER["lines"]:
        ws.append([tl["sl"], tl["item_code"], tl["title"], tl["unit"], tl["qty"]] + [next((l["rate"] for l in r["n"]["lines"] if l["sl"] == tl["sl"]), None) for r in rows])
    ws.append([]); ws.append(["", "", "Base ex-GST", "", ""] + [round(r["n"]["base_ex_gst"]) for r in rows])
    ws.append(["", "", "GST", "", ""] + [round(r["n"]["gst"]) for r in rows])
    ws.append(["", "", "Cost of payment terms (modelled)", "", ""] + [round(r["n"]["advance_cost"] + r["n"]["float_cost"]) for r in rows])
    ws.append(["", "", "LANDED", "", ""] + [round(r["n"]["landed"]) for r in rows])
    ws.append(["", "", "Score /100", "", ""] + [round(r["total"], 1) for r in rows])
    ws.append(["", "", "Rank (agent recommendation only)", "", ""] + [r["rank"] for r in rows])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=comparative_statement.xlsx"})


# ---- step 6
@app.get("/hod", response_class=HTMLResponse)
def hod(request: Request):
    c = db.con(); log = [dict(r) for r in c.execute("select * from mail_log where kind='hod' order by id desc limit 5")]
    dec = [dict(r) for r in c.execute("select * from decisions order by id desc limit 10")]; c.close()
    rows = rank(all_quotes(), weights(request))
    return page(request, "hod.html", "6", log=log, dec=dec, top=rows[0])


@app.post("/hod/send")
def hod_send(request: Request):
    rows = rank(all_quotes(), W)
    for r in rows: r["reasons"] = reasons(r, rows)
    summ, how = recommend.summary(rows)
    top = rows[0]
    base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/"))
    tok = recommend.sign(TENDER["id"], top["n"]["vendor"])
    body = f"""Recommendation for HOD review: {TENDER['project']} - {TENDER['package']}

{summ}

Ranking (score /100):
""" + "\n".join(f"  {r['rank']}. {r['n']['vendor']}: {r['total']:.1f}  landed Rs {r['n']['landed']:,.0f}" for r in rows) + f"""

Open the full comparative statement and reasons: {base}/compare

This is a recommendation only. Nothing is ordered or paid by this system.
Review and record your decision here: {base}/hod/{tok}
"""
    mailer.send("hod", mailer.HOD_ADDR, f"Vendor recommendation for approval: {TENDER['package']}", body)
    return RedirectResponse("/hod", 303)


@app.get("/hod/{token}", response_class=HTMLResponse)
def hod_review(request: Request, token: str):
    d = recommend.verify(token)
    if not d:
        return HTMLResponse("Link invalid or expired.", 400)
    rows = rank(all_quotes(), W)
    for r in rows: r["reasons"] = reasons(r, rows)
    return page(request, "hod_review.html", "6", d=d, rows=rows, token=token)


@app.post("/hod/{token}")
def hod_decide(token: str, decision: str = Form(...), note: str = Form("")):
    d = recommend.verify(token)
    if not d or decision not in ("approve", "reject", "send back"):
        return HTMLResponse("Invalid.", 400)
    c = db.con(); c.execute("insert into decisions(tender,decision,note,recommended) values(?,?,?,?)", (d["t"], decision, note, d["v"])); c.commit(); c.close()
    return RedirectResponse("/hod", 303)


@app.get("/mail", response_class=HTMLResponse)
def mail(request: Request):
    c = db.con(); log = [dict(r) for r in c.execute("select * from mail_log order by id desc")]; c.close()
    return page(request, "mail.html", "", log=log)


@app.get("/eval", response_class=HTMLResponse)
def eval_page(request: Request):
    from .evalq import run
    qs = [json.loads(f.read_text()) for d in ("cache", "eval_cache") for f in sorted((DATA_DIR / d).glob("*.json"))]
    return page(request, "eval.html", "", res=run(qs), synthetic=TENDER.get("synthetic"))


# ---- the agent: cases
from . import engine
STATE_LABEL = {"NEW": "reading", "CLARIFY": "clarifying with engineer", "MATCH": "matching item code", "ITEM_REVIEW": "waiting for HOD (item code)", "RFQ": "sending RFQs",
               "QUOTING": "collecting quotes", "COMPARE": "comparing", "AWAITING_HOD": "waiting for HOD decision", "IN_WORK": "work in progress", "CHECKIN": "post-job check-in", "DECIDED": "closed"}
T.env.globals["STATE_LABEL"] = STATE_LABEL


@app.get("/cases", response_class=HTMLResponse)
def cases(request: Request):
    c = db.con(); rows = [dict(r) for r in c.execute("select * from cases order by id desc")]; c.close()
    for r in rows: r["data"] = json.loads(r["data"] or "{}")
    return page(request, "cases.html", "A", rows=rows)


@app.post("/cases/new")
def cases_new(text: str = Form(...)):
    cid = engine.new_case(text.strip(), "web"); engine.bg(engine.advance, cid)
    return RedirectResponse(f"/cases/{cid}", 303)


@app.get("/cases/{cid}", response_class=HTMLResponse)
def case_page(request: Request, cid: int):
    ev, ch = engine.timeline(cid); c = engine._case(cid)
    return page(request, "case.html", "A", c=c, ev=ev, ch=ch)


@app.get("/cases/{cid}/fragment", response_class=HTMLResponse)
def case_fragment(request: Request, cid: int):
    ev, ch = engine.timeline(cid); c = engine._case(cid)
    return T.TemplateResponse(request, "case_fragment.html", {"c": c, "ev": ev, "ch": ch})


@app.post("/cases/{cid}/say")
def case_say(cid: int, role: str = Form(...), text: str = Form(...)):
    if role == "engineer": engine.bg(engine.on_engineer_message, cid, text)
    else: engine.log(cid, "hod", "received", text)
    return RedirectResponse(f"/cases/{cid}", 303)


@app.post("/cases/{cid}/button")
def case_button(cid: int, data: str = Form(...)):
    engine.bg(engine.on_hod_action, cid, data); return RedirectResponse(f"/cases/{cid}", 303)


def _demo_quotes():
    out = []
    for f in sorted((DATA_DIR / "quotes").glob("*.pdf")):
        q = json.loads((DATA_DIR / "cache" / (f.stem + ".json")).read_text()); v, _ = db.match_vendor(q["vendor_name"]); out.append((f, v["key"] if v else q["vendor_name"]))
    return out


def _vendor_email(cid, key, subject, body, attach=None):
    """A vendor writes back to the agent's inbox. With Mailpit this is a real email the watcher picks up; without it, injected directly."""
    from . import mailer
    st = mailer.send("vendor-reply", mailer.AGENT_ADDR, f"{subject} [Case #{cid}]", body, frm=f"rfq+{key}@rfq-test.example", attachments=[attach] if attach else None)
    return "logged only" not in st


@app.post("/cases/{cid}/simulate-vendors")
def case_sim(cid: int):
    def run():
        import time
        for f, key in _demo_quotes():
            time.sleep(1.5)
            if not _vendor_email(cid, key, "Re: SEEKING QUOTATION", "Please find our quotation attached.", f): engine.on_vendor_reply(cid, f, key)
    engine.bg(run); return RedirectResponse(f"/cases/{cid}", 303)


@app.post("/cases/{cid}/simulate-answers")
def case_answers(cid: int):
    canned = json.loads((DATA_DIR / "followup_replies.json").read_text()) if (DATA_DIR / "followup_replies.json").exists() else {}
    def run():
        import time
        d = engine._case(cid)["data"]
        for key, qs in (d.get("followups") or {}).items():
            if not qs or key in d.get("clarified", []): continue
            time.sleep(1.5); text = next((v for frag, v in canned.items() if frag in key), "Noted, we will confirm shortly.")
            if not _vendor_email(cid, key, "Re: Clarification needed on your quotation", text): engine.on_vendor_text_reply(cid, key, text)
    engine.bg(run); return RedirectResponse(f"/cases/{cid}", 303)


@app.post("/cases/{cid}/fastforward")
def case_ff(cid: int, hours: float = Form(24)):
    engine.bg(engine.advance_time, cid, hours); return RedirectResponse(f"/cases/{cid}", 303)


# ---- phone simulator: the same agent seen from the engineer's and the HOD's phones
from . import voice


def _sim_case(case: int | None):
    if case: return case
    c = db.con(); r = c.execute("select id from cases order by id desc limit 1").fetchone(); c.close(); return r["id"] if r else None


@app.get("/sim", response_class=HTMLResponse)
def sim(request: Request, case: int | None = None):
    cid = _sim_case(case)
    return page(request, "sim.html", "P", cid=cid, **_sim_ctx(cid))


def _sim_ctx(cid):
    if not cid: return {"eng": [], "hod": [], "c": None, "ev": []}
    ev, _ = engine.timeline(cid)
    return {"eng": engine.conversation(cid, "engineer"), "hod": engine.conversation(cid, "hod"), "c": engine._case(cid), "ev": ev}


@app.get("/sim/fragment", response_class=HTMLResponse)
def sim_fragment(request: Request, role: str = "engineer", case: int | None = None):
    cid = _sim_case(case); ctx = _sim_ctx(cid)
    return T.TemplateResponse(request, "sim_fragment.html", {"cid": cid, "role": role, "msgs": ctx["eng"] if role == "engineer" else ctx["hod"], "c": ctx["c"]})


@app.get("/sim/status", response_class=HTMLResponse)
def sim_status(request: Request, case: int | None = None):
    cid = _sim_case(case); c = engine._case(cid) if cid else None
    if not c: return HTMLResponse("")
    w = c["waiting"] or ("nobody, case closed" if c["state"] == "DECIDED" else "nobody, the agent is working")
    return HTMLResponse(f'State: <b>{STATE_LABEL.get(c["state"], c["state"])}</b> · waiting for: <b>{w}</b> · <a href="/cases/{cid}">full timeline and demo controls</a>')


@app.post("/sim/send")
def sim_send(text: str = Form(...), case: int | None = Form(None)):
    cid = _sim_case(case)
    def run():
        engine.engineer_inbound(text, "web", cid)
    engine.bg(run)
    return HTMLResponse("ok")


@app.post("/sim/voice")
async def sim_voice(file: UploadFile = File(...), case: int | None = Form(None)):
    data = await file.read()
    try:
        text = voice.to_text(data, file.filename or "voice.webm")
    except Exception as e:
        return HTMLResponse(f"Could not read the voice note: {e}", 400)
    cid = _sim_case(case); engine.bg(lambda: engine.engineer_inbound(text, "web (voice note)", cid))
    return HTMLResponse(text)


@app.post("/sim/button")
def sim_button(data: str = Form(...), case: int | None = Form(None)):
    engine.bg(engine.on_hod_action, _sim_case(case), data); return HTMLResponse("ok")


# ---- vendor performance ledger
@app.get("/vendors", response_class=HTMLResponse)
def vendors_page(request: Request):
    from .scoring import perf_score
    c = db.con(); vs = [dict(r) for r in c.execute("select * from vendors where category in ('waterproofing','road/civil') order by registered desc, name")]; c.close()
    for v in vs:
        v["hist"], v["sigs"] = db.history(v["key"]), db.signals(v["key"])
        v["pts"], v["txt"], v["stats"] = perf_score(v["hist"], v["sigs"], 30)
        v["synthetic"] = sum(1 for x in v["hist"] + v["sigs"] if "SYNTHETIC" in (x.get("source") or "")); v["observed"] = len(v["hist"]) + len(v["sigs"]) - v["synthetic"]
    return page(request, "vendors.html", "V", vs=sorted(vs, key=lambda v: -v["pts"]))


@app.get("/vendors/{key}", response_class=HTMLResponse)
def vendor_page(request: Request, key: str):
    from .scoring import perf_score
    c = db.con(); v = c.execute("select * from vendors where key=?", (key,)).fetchone(); c.close()
    if not v: return HTMLResponse("Unknown vendor", 404)
    hist, sigs = db.history(key), db.signals(key); pts, txt, stats = perf_score(hist, sigs, 30)
    return page(request, "vendor.html", "V", v=dict(v), hist=sorted(hist, key=lambda h: h.get("job_date") or ""), sigs=sigs, pts=pts, txt=txt)


@app.post("/vendors/{key}/job")
def vendor_job(key: str, quality: int = Form(...), delay_days: int = Form(0), dispute: int = Form(0), note: str = Form("")):
    from datetime import date
    db.add_job(key, 0, max(1, min(5, quality)), 1 if delay_days <= 3 else 0, delay_days, 1 if dispute else 0, note or "manual entry", "manual entry by HOD", date.today().isoformat())
    return RedirectResponse(f"/vendors/{key}", 303)
