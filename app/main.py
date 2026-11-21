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
STEPS = [("1", "Capture", "/requirement"), ("2", "Item code", "/requirement"), ("3", "RFQ", "/rfq"), ("4", "Quotes", "/quotes"), ("5", "Compare", "/compare"), ("6", "HOD", "/hod")]


@app.on_event("startup")
def boot():
    db.init()
    c = db.con()
    if c.execute("select count(*) from quotes").fetchone()[0] == 0 and CACHE.exists():  # cached extractions of the 4 received quotes (see README)
        for f in sorted(CACHE.glob("*.json")):
            q = json.loads(f.read_text()); v, _ = db.match_vendor(q["vendor_name"])
            c.execute("insert or replace into quotes values(?,?,?,CURRENT_TIMESTAMP)", ((v["key"] if v else q["vendor_name"]), f.name, json.dumps(q)))
        c.commit()
    c.close()


def page(request, name, active, **ctx):
    return T.TemplateResponse(request, name, {"steps": STEPS, "active": active, **ctx})


def all_quotes():
    c = db.con(); r = [json.loads(x["extracted"]) for x in c.execute("select extracted from quotes")]; c.close(); return r


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
    c = db.con(); c.execute("insert or replace into quotes values(?,?,?,CURRENT_TIMESTAMP)", ((v["key"] if v else q["vendor_name"]), file.filename, json.dumps(q))); c.commit(); c.close()
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
