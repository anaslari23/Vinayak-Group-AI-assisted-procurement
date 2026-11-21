import io, json, os, tempfile
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import openpyxl
from . import db, itemmatch, rfq, mailer, recommend
from .scoring import rank, reasons, W
from .normalise import ASSUMP
from .tender import TENDER
from .extract import extract_quote
from .parse_req import parse, transcribe
from . import voice

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Vinayak procurement agent")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
T = Jinja2Templates(directory=ROOT / "templates")
T.env.filters["inr"] = lambda v: f"{v:,.0f}" if v is not None else "-"
T.env.filters["inr2"] = lambda v: f"{v:,.2f}" if v is not None else "-"
from .tender import DATA_DIR
CACHE = DATA_DIR / "cache"
NAV = [("H", "Live flow", "/"), ("A", "Cases", "/cases"), ("V", "Vendors", "/vendors"), ("S", "Stock", "/stock"), ("W", "WhatsApp", "/whatsapp"), ("L", "Evidence", "/eval")]
STEPS = [("1", "Capture", "/requirement"), ("2", "Item code", "/requirement"), ("3", "RFQ", "/rfq"), ("4", "Quotes", "/quotes"), ("5", "Compare", "/compare"), ("6", "HOD", "/hod")]


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
    return T.TemplateResponse(request, name, {"steps": STEPS, "nav": NAV, "active": active, **ctx})


def all_quotes(case_id=0):
    c = db.con(); r = [json.loads(x["extracted"]) for x in c.execute("select extracted from quotes where case_id=?", (case_id,))]; c.close(); return r


def weights(request):
    try:
        w = {k: float(request.query_params.get(k, W[k])) for k in W}
    except ValueError:
        w = dict(W)
    s = sum(w.values()) or 1
    return {k: v * 100 / s for k, v in w.items()}


@app.get("/old-home", response_class=HTMLResponse)
def home_old(request: Request):
    c = db.con(); n = {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in ("requirements", "quotes", "mail_log", "decisions")}; c.close()
    return page(request, "home.html", "", n=n, tender=TENDER)


# ---- steps 1+2
@app.get("/requirement", response_class=HTMLResponse)
def req_form(request: Request):
    c = db.con(); rows = [dict(r) for r in c.execute("select id, state, data from cases order by id desc limit 25")]; c.close()
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}"); r["raw"] = r["data"].get("raw", ""); r["parsed"] = r["data"].get("parsed") or {}
    return page(request, "requirement.html", "1", rows=rows)


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
        m = itemmatch.match(parsed.get("item_description") or text, unit=parsed.get("uom"), quantity=parsed.get("quantity"), project=parsed.get("project"))
        itemmatch.log_run("form", "web user", parsed.get("project"), m)
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
    ie = DATA_DIR / "item_eval.json"
    return page(request, "eval.html", "L", res=run(qs), synthetic=TENDER.get("synthetic"), ie=json.loads(ie.read_text()) if ie.exists() else None)


# ---- the agent: cases
from . import engine
STATE_LABEL = {"NEW": "reading", "CLARIFY": "clarifying with engineer", "MATCH": "matching item code", "MATCH_ASK": "asking the engineer about the item", "CONFIRM": "confirming with the engineer", "ITEM_REVIEW": "waiting for HOD (item code)", "STOCK": "checking stock", "STOCK_REVIEW": "waiting for HOD (stock)", "RFQ": "sending RFQs",
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


# ---- Step 2 back office tools
@app.post("/requirement/accept")
def req_accept(text: str = Form(...), code: str = Form(...)):
    itemmatch.remember(text, code, who="back office (web)"); itemmatch.log_run("form", "web user", None, {"raw_text": text, "status": "human_choice", "candidates": [], "flags": [], "erp_loaded": itemmatch.erp().loaded}, "back office (web)", code)
    return RedirectResponse("/requirement", 303)


@app.get("/erp-cleanup", response_class=HTMLResponse)
def erp_cleanup(request: Request):
    rep = itemmatch.cleanup_report(itemmatch.flag_hits())
    c = db.con(); syn = [dict(r) for r in c.execute("select * from synonyms order by word")]; ch = [dict(r) for r in c.execute("select * from item_choices order by id desc limit 15")]
    log = [dict(r) for r in c.execute("select * from item_log order by id desc limit 15")]; c.close()
    return page(request, "cleanup.html", "E", rep=rep, syn=syn, ch=ch, log=log, loaded=itemmatch.erp().loaded, rows=len(itemmatch.erp().rows))


@app.get("/erp-cleanup.csv")
def erp_cleanup_csv():
    import csv
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["flag", "codes", "detail", "times_hit"])
    for r in itemmatch.cleanup_report(itemmatch.flag_hits()): w.writerow([r["flag"], " | ".join(r["codes"]), r["detail"], r["hits"]])
    return StreamingResponse(io.BytesIO(buf.getvalue().encode()), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=erp_cleanup.csv"})


@app.post("/synonyms")
def synonyms_add(word: str = Form(...), maps_to: str = Form(...)):
    c = db.con(); c.execute("insert or replace into synonyms values(?,?)", (word.strip().lower(), maps_to.strip().lower())); c.commit(); c.close()
    return RedirectResponse("/erp-cleanup", 303)


# ---- master workbook and stock
from . import master, stock as stockmod


@app.get("/master", response_class=HTMLResponse)
def master_page(request: Request, msg: str = ""):
    c = db.con(); counts = {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in ("sites", "items", "materials", "stock", "usage", "vendors", "vendor_history", "vendor_signals", "stock_moves")}
    c.close()
    try: _, issues = master.read()
    except Exception as e: issues = [{"sheet": "-", "row": "-", "problem": f"could not read the workbook: {e}"}]
    return page(request, "master.html", "M", counts=counts, issues=issues, meta=master.meta(), path=str(master.PATH.name), msg=msg)


@app.post("/master/upload")
async def master_upload(file: UploadFile = File(...)):
    import shutil
    data = await file.read()
    tmp = master.PATH.with_suffix(".upload.xlsx"); tmp.write_bytes(data)
    try:
        master.read(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True); return RedirectResponse(f"/master?msg=Could+not+read+that+file:+{e}", 303)
    if master.PATH.exists(): shutil.copy(master.PATH, master.PATH.with_suffix(".bak.xlsx"))
    tmp.replace(master.PATH); r = master.sync()
    return RedirectResponse(f"/master?msg=Loaded+{sum(r['counts'].values())}+rows,+{len(r['issues'])}+issue(s)", 303)


@app.get("/master/download")
def master_download(current: int = 0):
    if current:
        p = master.PATH.with_name("export_current.xlsx"); master.export(p)
    else:
        p = master.PATH
    return StreamingResponse(open(p, "rb"), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={'vinayak_master_current.xlsx' if current else 'vinayak_master.xlsx'}"})


@app.get("/stock", response_class=HTMLResponse)
def stock_page(request: Request, q: str = ""):
    c = db.con()
    st = [dict(r) for r in c.execute("select s.*, m.name from stock s join materials m on m.code=s.material_code order by s.site, m.name")]
    us = [dict(r) for r in c.execute("select u.*, m.name from usage u join materials m on m.code=u.material_code order by u.used_on desc limit 20")]
    mv = [dict(r) for r in c.execute("select * from stock_moves order by id desc limit 10")]; c.close()
    for r in st: r["free"] = max(0, (r["qty"] or 0) - (r["reserved"] or 0))
    return page(request, "stock.html", "S", st=st, us=us, mv=mv, q=q, ans=stockmod.answer(q) if q else None)


# ---- the calling agent
import asyncio, base64 as _b64
from . import call as callmod


@app.get("/call", response_class=HTMLResponse)
def call_page(request: Request):
    vk = bool(os.getenv("VONAGE_APPLICATION_ID")); pk = bool(os.getenv("PLIVO_AUTH_ID") and os.getenv("PLIVO_FROM"))
    prov = "vonage" if vk else ("plivo" if pk else "")
    allowed = [x.strip() for x in os.getenv("VONAGE_ALLOWED_TO" if vk else "PLIVO_ALLOWED_TO", "").split(",") if x.strip()]
    return page(request, "call.html", "C", langs=voice.LANGS, plivo=bool(prov), prov=prov, allowed=allowed)


@app.post("/call/turn")
async def call_turn(file: UploadFile = File(...), call_id: str = Form("")):
    data = await file.read()
    try:
        r = await asyncio.to_thread(callmod.turn, call_id or None, data, file.filename or "voice.webm")   # off the event loop: a turn takes seconds
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 400)
    wav = r.pop("wav"); r["audio_b64"] = _b64.b64encode(wav).decode(); AUDIO[r["call_id"]] = wav
    return r


AUDIO: dict = {}


@app.get("/call/audio/{call_id}.wav")
def call_audio(call_id: str):
    from fastapi.responses import Response
    return Response(AUDIO.get(call_id, b""), media_type="audio/wav")


# ---- Telephony: a phone number's voice webhook points here (Twilio-style TwiML). Built to Twilio's documented behaviour; NOT yet tested on a live call.
# Twilio abandons a webhook that takes longer than ~15 s and a turn takes 6-17 s, so each turn runs in the background and the caller's line polls for it.
import asyncio, hashlib, hmac, threading, time as _time
TEL: dict = {}


def _twiml(body: str):
    from fastapi.responses import Response
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


async def _twilio_ok(request: Request):
    """If TWILIO_VALIDATE=1, only accept requests really signed by Twilio (X-Twilio-Signature, HMAC-SHA1 over URL + sorted params)."""
    if os.getenv("TWILIO_VALIDATE") != "1": return True
    form = await request.form(); url = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/")) + request.url.path
    if request.url.query: url += "?" + request.url.query
    data = url + "".join(k + str(form[k]) for k in sorted(form.keys()))
    mac = _b64.b64encode(hmac.new(os.environ.get("TWILIO_AUTH_TOKEN", "").encode(), data.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(mac, request.headers.get("X-Twilio-Signature", ""))


def _play(key: str, wav: bytes, base: str):
    AUDIO[key] = voice.to_phone_wav(wav); return f"<Play>{base}/call/audio/{key}.wav</Play>"


def _record(base: str):
    return f'<Record action="{base}/telephony/recording" method="POST" maxLength="30" timeout="4" playBeep="false" trim="trim-silence"/>'


@app.post("/telephony/voice")
async def tel_voice(request: Request):
    if not await _twilio_ok(request): return HTMLResponse("forbidden", 403)
    form = await request.form(); base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/"))
    if "greet" not in AUDIO:
        AUDIO["greet"] = voice.to_phone_wav(voice.tts("नमस्ते, मैं विनायक ग्रुप का प्रोक्योरमेंट असिस्टेंट हूँ। बताइए, किस सामग्री की ज़रूरत है? आप किसी भी भाषा में बोल सकते हैं।", "hi-IN"))
    TEL[str(form.get("CallSid"))] = {"from": str(form.get("From", "")), "n": 0}
    return _twiml(f"<Play>{base}/call/audio/greet.wav</Play>{_record(base)}")


def _fetch_recording(url: str) -> bytes:
    import httpx
    auth = (os.environ.get("TWILIO_ACCOUNT_SID", ""), os.environ.get("TWILIO_AUTH_TOKEN", ""))
    last = None
    for _ in range(6):   # the media can take a moment to become available after the webhook fires
        r = httpx.get(url + ".wav", auth=auth if auth[0] else None, timeout=30, follow_redirects=True)
        if r.status_code == 200 and len(r.content) > 500: return r.content
        last = r.status_code; _time.sleep(1)
    raise RuntimeError(f"recording not available (HTTP {last})")


@app.post("/telephony/recording")
async def tel_recording(request: Request):
    if not await _twilio_ok(request): return HTMLResponse("forbidden", 403)
    form = await request.form(); sid = str(form.get("CallSid", "")); url = str(form.get("RecordingUrl", "")); base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/"))
    t = TEL.setdefault(sid, {"from": "", "n": 0}); t["n"] += 1; n = t["n"]
    if not url:
        return _twiml(_play(f"{sid}m{n}", voice.tts("Sorry, I did not hear anything. Please say it again.", "en-IN"), base) + _record(base))
    def run():
        try:
            audio = _fetch_recording(url); r = callmod.turn(sid, audio, "call.wav")
            if t["n"] == 1 and r.get("case_id"): engine.log(r["case_id"], "system", "call", f"Phone call from {t['from'] or 'unknown number'}")
            TEL[sid + f":{n}"] = r
        except Exception as e:
            TEL[sid + f":{n}"] = {"error": f"{type(e).__name__}: {e}"}
    threading.Thread(target=run, daemon=True).start()
    return _twiml(f'<Redirect method="POST">{base}/telephony/poll?sid={sid}&amp;n={n}</Redirect>')


@app.post("/telephony/poll")
async def tel_poll(request: Request, sid: str, n: int):
    if not await _twilio_ok(request): return HTMLResponse("forbidden", 403)
    base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/"))
    for _ in range(18):                       # hold up to ~9 s, well inside Twilio's 15 s limit
        r = TEL.get(f"{sid}:{n}")
        if r: break
        await asyncio.sleep(0.5)
    else:
        return _twiml(f'<Pause length="1"/><Redirect method="POST">{base}/telephony/poll?sid={sid}&amp;n={n}</Redirect>')
    TEL.pop(f"{sid}:{n}", None)
    if r.get("error"):
        return _twiml(_play(f"{sid}e{n}", voice.tts("Sorry, something went wrong on my side. Please call again.", "en-IN"), base) + "<Hangup/>")
    play = _play(f"{sid}r{n}", r["wav"], base)
    return _twiml(play + ("<Hangup/>" if r["done"] else _record(base)))


# ---- Plivo: the AGENT CALLS the engineer's phone (free trial can call verified numbers). Built to Plivo's documented XML/REST; first live call pending.
PLIVO_SESS: dict = {}


def _plivo_xml(body: str):
    from fastapi.responses import Response
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


def _plivo_record(base: str, sid: str):
    return f'<Record action="{base}/telephony/plivo/recording?sid={sid}" method="POST" maxLength="30" timeout="4" playBeep="false" redirect="true" fileFormat="wav"/>'


@app.post("/telephony/plivo/call")
async def plivo_call(request: Request, to: str = Form(...)):
    import httpx
    aid, tok, frm = os.getenv("PLIVO_AUTH_ID"), os.getenv("PLIVO_AUTH_TOKEN"), os.getenv("PLIVO_FROM")
    if not (aid and tok and frm): return JSONResponse({"error": "Plivo is not configured (PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_FROM)."}, 400)
    to = to.strip().replace(" ", "")
    allowed = [x.strip().replace(" ", "") for x in os.getenv("PLIVO_ALLOWED_TO", "").split(",") if x.strip()]
    if to not in allowed: return JSONResponse({"error": "That number is not on the allowed list (PLIVO_ALLOWED_TO). The agent only calls numbers you have allowed."}, 403)
    import uuid
    sid = uuid.uuid4().hex[:10]; base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/")); PLIVO_SESS[sid] = {"to": to}
    r = await asyncio.to_thread(lambda: httpx.post(f"https://api.plivo.com/v1/Account/{aid}/Call/", auth=(aid, tok), timeout=30,
                                                    json={"from": frm, "to": to, "answer_url": f"{base}/telephony/plivo/answer?sid={sid}", "answer_method": "POST", "time_limit": 600}))
    return JSONResponse({"status": r.status_code, "response": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]}, 200 if r.status_code < 300 else 400)


@app.api_route("/telephony/plivo/answer", methods=["GET", "POST"])
async def plivo_answer(request: Request, sid: str = ""):
    if sid not in PLIVO_SESS: return HTMLResponse("unknown call", 403)
    base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/"))
    if "greet" not in AUDIO:
        AUDIO["greet"] = voice.to_phone_wav(await asyncio.to_thread(voice.tts, "नमस्ते, मैं विनायक ग्रुप का प्रोक्योरमेंट असिस्टेंट हूँ। बताइए, किस सामग्री की ज़रूरत है? आप किसी भी भाषा में बोल सकते हैं।", "hi-IN"))
    return _plivo_xml(f"<Play>{base}/call/audio/greet.wav</Play>{_plivo_record(base, sid)}")


@app.api_route("/telephony/plivo/recording", methods=["GET", "POST"])
async def plivo_recording(request: Request, sid: str = ""):
    import httpx
    if sid not in PLIVO_SESS: return HTMLResponse("unknown call", 403)
    form = await request.form() if request.method == "POST" else {}
    url = str(form.get("RecordUrl") or request.query_params.get("RecordUrl") or ""); base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/"))
    s = PLIVO_SESS[sid]; s["n"] = s.get("n", 0) + 1; n = s["n"]
    if not url:
        AUDIO[f"{sid}m{n}"] = voice.to_phone_wav(await asyncio.to_thread(voice.tts, "Sorry, I did not hear anything. Please say it again.", "en-IN"))
        return _plivo_xml(f"<Play>{base}/call/audio/{sid}m{n}.wav</Play>{_plivo_record(base, sid)}")
    def work():
        audio = None
        for _ in range(6):                      # the recording file can take a moment to become available
            r = httpx.get(url, timeout=30, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 500: audio = r.content; break
            _time.sleep(1)
        if audio is None: raise RuntimeError("recording not available")
        r = callmod.turn(sid, audio, "call.wav")
        if n == 1 and r.get("case_id"): engine.log(r["case_id"], "system", "call", f"Phone call to {s['to']} (agent-initiated, Plivo)")
        return r
    try:
        r = await asyncio.to_thread(work)
    except Exception as e:
        AUDIO[f"{sid}e{n}"] = voice.to_phone_wav(await asyncio.to_thread(voice.tts, "Sorry, something went wrong on my side. I will message you instead.", "en-IN"))
        return _plivo_xml(f"<Play>{base}/call/audio/{sid}e{n}.wav</Play><Hangup/>")
    AUDIO[f"{sid}r{n}"] = voice.to_phone_wav(r["wav"])
    return _plivo_xml(f"<Play>{base}/call/audio/{sid}r{n}.wav</Play>" + ("<Hangup/>" if r["done"] else _plivo_record(base, sid)))


# ---- Vonage: the AGENT CALLS the engineer's phone. Trial: calls to your registered number from a test caller ID.
# Pattern: stream greeting -> record (event webhook) -> hold the line on silence -> when the turn is ready, TRANSFER the live call to a new NCCO. Built to Vonage's docs; first live call pending.
VON: dict = {}


def _vonage_jwt():
    import jwt, time, uuid
    key = Path(os.getenv("VONAGE_PRIVATE_KEY_PATH", str(ROOT.parent / "vonage_private.key"))).read_text()
    return jwt.encode({"application_id": os.environ["VONAGE_APPLICATION_ID"], "iat": int(time.time()), "exp": int(time.time()) + 600, "jti": str(uuid.uuid4())}, key, algorithm="RS256")


def _ncco_listen(base: str, sid: str, first: str):
    """Play something, then record the caller; while we think, a silent loop holds the line until we transfer the call."""
    return [{"action": "stream", "streamUrl": [first]},
            {"action": "record", "format": "wav", "endOnSilence": 3, "beepStart": False, "timeOut": 30, "eventUrl": [f"{base}/telephony/vonage/recording?sid={sid}"], "eventMethod": "POST"},
            {"action": "stream", "streamUrl": [f"{base}/call/audio/silence.wav"], "loop": 0}]


@app.post("/telephony/vonage/call")
async def vonage_call(request: Request, to: str = Form(...)):
    import httpx, uuid
    if not (os.getenv("VONAGE_APPLICATION_ID") and (Path(os.getenv("VONAGE_PRIVATE_KEY_PATH", str(ROOT.parent / "vonage_private.key"))).exists())):
        return JSONResponse({"error": "Vonage is not configured (VONAGE_APPLICATION_ID and the private key file)."}, 400)
    to = to.strip().replace(" ", "").lstrip("+")
    allowed = [x.strip().replace(" ", "").lstrip("+") for x in os.getenv("VONAGE_ALLOWED_TO", "").split(",") if x.strip()]
    if to not in allowed: return JSONResponse({"error": "That number is not on the allowed list (VONAGE_ALLOWED_TO). The agent only calls numbers you have allowed."}, 403)
    sid = uuid.uuid4().hex[:10]; base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/")); VON[sid] = {"to": to}
    if "greet" not in AUDIO:
        AUDIO["greet"] = voice.to_phone_wav(await asyncio.to_thread(voice.tts, "नमस्ते, मैं विनायक ग्रुप का प्रोक्योरमेंट असिस्टेंट हूँ। बताइए, किस सामग्री की ज़रूरत है? आप किसी भी भाषा में बोल सकते हैं।", "hi-IN"))
    AUDIO.setdefault("silence", voice.to_phone_wav(b"") if False else _silence())
    body = {"to": [{"type": "phone", "number": to}], "from": {"type": "phone", "number": os.getenv("VONAGE_FROM", "123456789")},
            "ncco": _ncco_listen(base, sid, f"{base}/call/audio/greet.wav"), "event_url": [f"{base}/telephony/vonage/event?sid={sid}"], "event_method": "POST"}
    r = await asyncio.to_thread(lambda: httpx.post("https://api.nexmo.com/v1/calls", headers={"Authorization": "Bearer " + _vonage_jwt()}, json=body, timeout=30))
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:300]}
    if r.status_code < 300: VON[sid]["uuid"] = j.get("uuid")
    return JSONResponse({"status": r.status_code, "response": j}, 200 if r.status_code < 300 else 400)


def _silence():
    import struct, io, wave
    b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(struct.pack("<h", 0) * 16000); w.close(); return b.getvalue()


@app.post("/telephony/vonage/event")
async def vonage_event(request: Request, sid: str = ""):
    try: ev = await request.json()
    except Exception: ev = {}
    if sid in VON:
        VON[sid].setdefault("events", []).append(ev.get("status") or ev.get("type")); VON[sid]["uuid"] = VON[sid].get("uuid") or ev.get("uuid")
        if ev.get("status") in ("completed", "failed", "rejected", "busy", "unanswered", "timeout"): print(f"[vonage] call {sid} {ev.get('status')}: {json.dumps(ev)[:400]}", flush=True)
    return JSONResponse({})


@app.post("/telephony/vonage/recording")
async def vonage_recording(request: Request, sid: str = ""):
    """Recording finished: run the turn in the background, then transfer the live call to the reply + the next listen."""
    import httpx
    if sid not in VON: return JSONResponse({}, 403)
    ev = await request.json(); url = ev.get("recording_url"); base = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/"))
    s = VON[sid]; s["n"] = s.get("n", 0) + 1; n = s["n"]

    def transfer(ncco):
        httpx.put(f"https://api.nexmo.com/v1/calls/{s['uuid']}", headers={"Authorization": "Bearer " + _vonage_jwt()}, json={"action": "transfer", "destination": {"type": "ncco", "ncco": ncco}}, timeout=30)

    def work():
        try:
            audio = None
            for _ in range(6):
                r = httpx.get(url, headers={"Authorization": "Bearer " + _vonage_jwt()}, timeout=30, follow_redirects=True)
                if r.status_code == 200 and len(r.content) > 500: audio = r.content; break
                _time.sleep(1)
            if audio is None: raise RuntimeError("recording not available")
            r = callmod.turn(sid, audio, "call.wav")
            if n == 1 and r.get("case_id"): engine.log(r["case_id"], "system", "call", f"Phone call to +{s['to']} (agent-initiated, Vonage)")
            AUDIO[f"{sid}r{n}"] = voice.to_phone_wav(r["wav"]); play = f"{base}/call/audio/{sid}r{n}.wav"
            transfer([{"action": "stream", "streamUrl": [play]}] + ([] if r["done"] else _ncco_listen(base, sid, f"{base}/call/audio/silence.wav")[1:]))
            if r["done"]: s["done"] = True
        except Exception as e:
            print(f"[vonage] turn failed: {type(e).__name__}: {e}", flush=True)
            try:
                AUDIO[f"{sid}e{n}"] = voice.to_phone_wav(voice.tts("Sorry, something went wrong on my side. I will message you instead.", "en-IN"))
                transfer([{"action": "stream", "streamUrl": [f"{base}/call/audio/{sid}e{n}.wav"]}])
            except Exception: pass
    threading.Thread(target=work, daemon=True).start()
    return JSONResponse({})



# ---- WhatsApp group bridge (Baileys sidecar in /whatsapp talks to these)
from . import whatsapp as wa


def _bridge_ok(request: Request):
    k = os.getenv("WA_BRIDGE_KEY", "")
    return bool(k) and request.headers.get("x-bridge-key") == k


@app.post("/whatsapp/inbound")
async def wa_inbound(request: Request):
    if not _bridge_ok(request): return JSONResponse({"error": "forbidden"}, 403)
    p = await request.json()
    return await asyncio.to_thread(wa.inbound, p)


@app.get("/whatsapp/outbox")
async def wa_outbox(request: Request):
    if not _bridge_ok(request): return JSONResponse({"error": "forbidden"}, 403)
    return {"items": await asyncio.to_thread(wa.outbox)}


@app.post("/whatsapp/ack")
async def wa_ack(request: Request):
    if not _bridge_ok(request): return JSONResponse({"error": "forbidden"}, 403)
    wa.ack((await request.json()).get("ids", [])); return {"ok": True}


@app.post("/whatsapp/status")
async def wa_status(request: Request):
    if not _bridge_ok(request): return JSONResponse({"error": "forbidden"}, 403)
    j = await request.json(); wa.STATUS.update({"state": j.get("state"), "qr": j.get("qr") if j.get("state") == "qr" else None, "me": j.get("me") or wa.STATUS.get("me"), "lid": j.get("lid") or wa.STATUS.get("lid")}); return {"ok": True}


@app.get("/whatsapp", response_class=HTMLResponse)
def wa_page(request: Request):
    c = db.con()
    groups = [dict(r) for r in c.execute("select jid, group_name, count(*) n, sum(handled like 'raised%' or handled like 'noticed%') raised, max(ts) last from wa_messages where jid like '%@g.us' group by jid order by last desc")]
    msgs = [dict(r) for r in c.execute("select * from wa_messages order by id desc limit 25")]
    cases = [dict(r) for r in c.execute("select w.*, c.state, c.waiting from wa_cases w join cases c on c.id=w.case_id order by w.case_id desc limit 10")]; c.close()
    return page(request, "whatsapp.html", "W", st=wa.STATUS, groups=groups, msgs=msgs, cases=cases)



# ---- LiveKit: real-time voice. The worker in /livekit_agent does the audio (Sarvam speech in and out); this is its brain and the browser page's token.
@app.post("/livekit/turn")
async def livekit_turn(request: Request):
    if not _bridge_ok(request): return JSONResponse({"error": "forbidden"}, 403)
    p = await request.json()
    r = await asyncio.to_thread(callmod.turn_text, p["session"], p.get("text", ""), p.get("lang") or "en-IN")
    return {"reply": r["reply"], "lang": r["lang"], "done": r["done"], "case_id": r["case_id"]}


@app.get("/livekit/token")
def livekit_token(request: Request):
    import jwt, time, uuid
    key, sec, url = os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"), os.getenv("LIVEKIT_URL")
    if not (key and sec and url): return JSONResponse({"error": "LiveKit is not configured (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)."}, 400)
    room = "call-" + uuid.uuid4().hex[:8]; ident = "caller-" + uuid.uuid4().hex[:6]
    tok = jwt.encode({"iss": key, "sub": ident, "name": "Site engineer", "nbf": int(time.time()) - 5, "exp": int(time.time()) + 3600,
                      "video": {"room": room, "roomJoin": True, "canPublish": True, "canSubscribe": True, "canPublishData": True}}, sec, algorithm="HS256")
    return {"token": tok, "url": url, "room": room}


@app.get("/livekit", response_class=HTMLResponse)
def livekit_page(request: Request):
    return page(request, "livekit.html", "K", configured=bool(os.getenv("LIVEKIT_API_KEY") and os.getenv("LIVEKIT_URL")))



# ---- live flow: the home page. Polls /api/flow and animates what the agent actually did.
from . import flow as flowmod


@app.get("/api/flow")
def api_flow(case: int | None = None):
    cid = case or flowmod.latest_case()
    if not cid: return {"case_id": None}
    try: return flowmod.build(cid)
    except KeyError: return {"case_id": None}


@app.get("/api/cases")
def api_cases():
    c = db.con(); rows = [dict(r) for r in c.execute("select id, state, data from cases order by id desc limit 12")]; c.close()
    from .flow import ORDER
    return [{"id": r["id"], "state": r["state"], "pct": round(100 * min(ORDER.get(r["state"], 0), 9) / 9), "raw": (json.loads(r["data"] or "{}").get("raw", ""))[:60]} for r in rows]


@app.post("/api/send")
async def api_send(request: Request):
    p = await request.json(); text = (p.get("text") or "").strip()
    if not text: return JSONResponse({"error": "empty"}, 400)
    cid = engine.new_case(text, "web (live page)"); engine.bg(engine.advance, cid); return {"case_id": cid}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return page(request, "live.html", "H")
