"""The master workbook: the editable source of truth. Load, validate, sync into the app's tables, export back to Excel.
Validation never blocks the app: bad rows are reported (sheet, row, reason) and skipped."""
import os, re
from datetime import datetime
from pathlib import Path
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill
from . import db
from .tender import DATA_DIR

PATH = Path(os.getenv("MASTER_XLSX", str(DATA_DIR / "vinayak_master.xlsx")))
ALLOWED_VENDOR_DOMAIN = "@rfq-test.example"
REQUIRED = {"Sites": ["site"], "Items": ["code", "description"], "Materials": ["code", "name", "unit"], "Stock": ["site", "material_code", "qty_on_hand"],
            "Usage": ["date", "site", "material_code", "qty"], "Vendors": ["name", "category"], "Vendor_history": ["vendor", "quality_1_5"], "Vendor_rounds": ["vendor"], "Synonyms": ["word", "maps_to"]}


def _rows(ws):
    it = ws.iter_rows(values_only=True); head = next(it, None)
    if not head: return [], []
    cols = [str(h).strip() if h is not None else "" for h in head]
    out = []
    for n, r in enumerate(it, start=2):
        if r is None or all(c is None or str(c).strip() == "" for c in r): continue
        out.append((n, {cols[i]: (r[i] if i < len(r) else None) for i in range(len(cols)) if cols[i]}))
    return cols, out


def _num(v, default=None):
    try: return float(v)
    except (TypeError, ValueError): return default


def _day(v):
    if isinstance(v, datetime): return v.date().isoformat()
    if v is None or str(v).strip() == "": return None
    s = str(v).strip()[:10]
    try: datetime.fromisoformat(s); return s
    except ValueError: return None


def read(path: Path = None):
    """Parse and validate. Returns (data dict, issues list). Never raises on bad content."""
    path = path or PATH; issues = []; data = {}
    wb = load_workbook(path, data_only=True)
    for name, req in REQUIRED.items():
        if name not in wb.sheetnames:
            issues.append({"sheet": name, "row": "-", "problem": "sheet missing (skipped)"}); data[name] = []; continue
        cols, rows = _rows(wb[name]); missing = [c for c in req if c not in cols]
        if missing:
            issues.append({"sheet": name, "row": 1, "problem": f"missing column(s): {', '.join(missing)}. Sheet skipped."}); data[name] = []; continue
        data[name] = rows
    sites = {r["site"] for _, r in data["Sites"]}; mats = {str(r["code"]).strip() for _, r in data["Materials"]}
    seen = set()
    for n, r in data["Items"]:
        k = (str(r["code"]).strip(), str(r["description"]).strip())
        if k in seen: pass  # exact repeats are real-world ERP mess, kept and flagged later by the matcher
        seen.add(k)
    for n, r in data["Stock"]:
        if _num(r["qty_on_hand"]) is None or _num(r["qty_on_hand"]) < 0: issues.append({"sheet": "Stock", "row": n, "problem": "qty_on_hand is not a non-negative number (row skipped)"})
        if r["site"] not in sites: issues.append({"sheet": "Stock", "row": n, "problem": f"site '{r['site']}' is not in the Sites sheet"})
        if str(r["material_code"]).strip() not in mats: issues.append({"sheet": "Stock", "row": n, "problem": f"material_code '{r['material_code']}' is not in the Materials sheet"})
        if (_num(r.get("reserved"), 0) or 0) > (_num(r["qty_on_hand"], 0) or 0): issues.append({"sheet": "Stock", "row": n, "problem": "reserved is more than qty_on_hand"})
    for n, r in data["Usage"]:
        if _day(r["date"]) is None: issues.append({"sheet": "Usage", "row": n, "problem": "date is not a valid date (row skipped)"})
        if str(r["material_code"]).strip() not in mats: issues.append({"sheet": "Usage", "row": n, "problem": f"material_code '{r['material_code']}' is not in the Materials sheet"})
    names = [str(r["name"]).strip().lower() for _, r in data["Vendors"]]
    for n, r in data["Vendors"]:
        if names.count(str(r["name"]).strip().lower()) > 1: issues.append({"sheet": "Vendors", "row": n, "problem": f"duplicate vendor '{r['name']}'"})
        em = str(r.get("test_email") or "").strip()
        if em and not em.lower().endswith(ALLOWED_VENDOR_DOMAIN): issues.append({"sheet": "Vendors", "row": n, "problem": f"'{em}' is not a test address; ignored and replaced by a test inbox (the agent never stores real vendor addresses)"})
    return data, issues


def sync(path: Path = None, reset_items=True) -> dict:
    """Load the workbook into the database. Workbook-origin rows are replaced; rows the agent recorded itself (origin 'agent') are kept."""
    path = path or PATH
    data, issues = read(path); c = db.con(); counts = {}
    c.execute("delete from items"); c.execute("delete from vendors"); c.execute("delete from sites"); c.execute("delete from materials"); c.execute("delete from stock"); c.execute("delete from usage")
    c.execute("delete from vendor_history where origin='workbook'"); c.execute("delete from vendor_signals where origin='workbook'")
    for _, r in data["Sites"]: c.execute("insert or replace into sites values(?,?,?,?)", (r["site"], r.get("project"), r.get("city"), r.get("note")))
    for _, r in data["Items"]: c.execute("insert into items(code,descr) values(?,?)", (str(r["code"]).strip(), " ".join(str(r["description"]).split())))
    for _, r in data["Materials"]: c.execute("insert or replace into materials values(?,?,?,?,?)", (str(r["code"]).strip(), r["name"], r.get("category"), r.get("unit"), (r.get("synonyms") or "")))
    for _, r in data["Stock"]:
        q = _num(r["qty_on_hand"])
        if q is None or q < 0: continue
        c.execute("insert into stock(site,store,material_code,qty,unit,reserved,updated,source) values(?,?,?,?,?,?,?,?)", (r["site"], r.get("store"), str(r["material_code"]).strip(), q, r.get("unit"), _num(r.get("reserved"), 0), _day(r.get("last_updated")), r.get("source")))
    for _, r in data["Usage"]:
        d = _day(r["date"]); q = _num(r["qty"])
        if d is None or q is None: continue
        c.execute("insert into usage(used_on,site,location,material_code,qty,unit,work_order,source) values(?,?,?,?,?,?,?,?)", (d, r["site"], r.get("where_used"), str(r["material_code"]).strip(), q, r.get("unit"), r.get("work_order"), r.get("source")))
    for _, r in data["Vendors"]:
        n = str(r["name"]).strip(); k = db.vkey(n); em = str(r.get("test_email") or "").strip()
        em = em if em.lower().endswith(ALLOWED_VENDOR_DOMAIN) else f"rfq+{k}@rfq-test.example"   # a real address can never get in
        c.execute("insert or ignore into vendors values(?,?,?,?,?,?)", (k, n, r["category"], 1 if str(r.get("registered", "yes")).strip().lower() in ("yes", "y", "1", "true") else 0, em, r.get("category_source") or ""))
    def vkey_of(name):
        row = c.execute("select key from vendors where lower(name)=lower(?)", (str(name).strip(),)).fetchone(); return row["key"] if row else db.vkey(str(name))
    for _, r in data["Vendor_history"]:
        c.execute("insert into vendor_history(vendor_key,project,work,quality,on_time,delay_days,disputes,note,source,job_date,origin) values(?,?,?,?,?,?,?,?,?,?,'workbook')",
                  (vkey_of(r["vendor"]), r.get("project"), r.get("work"), int(_num(r["quality_1_5"], 3)), int(_num(r.get("on_time_1_0"), 1)), int(_num(r.get("delay_days"), 0)), int(_num(r.get("dispute_1_0"), 0)), r.get("note"), r.get("source") or "workbook", _day(r.get("job_date"))))
    for _, r in data["Vendor_rounds"]:
        c.execute("insert into vendor_signals(vendor_key,case_id,responded,hours,chased,issues,vs_lowest_pct,source,origin) values(?,?,?,?,?,?,?,?,'workbook')",
                  (vkey_of(r["vendor"]), 0, int(_num(r.get("responded_1_0"), 1)), _num(r.get("hours_to_reply")), int(_num(r.get("chased_1_0"), 0)), _num(r.get("open_questions")), _num(r.get("price_vs_lowest_pct")), r.get("source") or "workbook"))
    for _, r in data["Synonyms"]: c.execute("insert or replace into synonyms values(?,?)", (str(r["word"]).strip().lower(), str(r["maps_to"]).strip().lower()))
    for t in ("sites", "items", "materials", "stock", "usage", "vendors"): counts[t] = c.execute(f"select count(*) from {t}").fetchone()[0]
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    c.execute("insert or replace into master_meta values('loaded_file', ?)", (str(path),)); c.execute("insert or replace into master_meta values('file_mtime', ?)", (str(path.stat().st_mtime),))
    c.execute("insert or replace into master_meta values('loaded_at', ?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)); c.execute("insert or replace into master_meta values('erp_loaded', ?)", (stamp,))
    c.commit(); c.close()
    try:
        from . import itemmatch; itemmatch.erp(reload=True)
    except Exception:
        pass
    return {"counts": counts, "issues": issues, "file": str(path)}


def meta():
    c = db.con(); m = {r["key"]: r["value"] for r in c.execute("select * from master_meta")}; c.close(); return m


def check_reload():
    """Called by the scheduler: if the workbook file changed on disk, reload it."""
    try:
        if PATH.exists() and str(PATH.stat().st_mtime) != meta().get("file_mtime"):
            r = sync(); return r
    except Exception as e:
        print("master reload error:", e)
    return None


def export(path: Path):
    """Write the app's current state back to an Excel file (includes what the agent recorded: ledger rows and stock-transfer requests)."""
    c = db.con(); wb = Workbook(); head = PatternFill("solid", fgColor="E8E2D5")
    def sh(name, cols, rows, first=False):
        ws = wb.active if first else wb.create_sheet(); ws.title = name; ws.append(cols)
        for x in ws[1]: x.font, x.fill = Font(bold=True), head
        for r in rows: ws.append(list(r))
    sh("Sites", ["site", "project", "city", "note"], [tuple(r) for r in c.execute("select name,project,city,note from sites")], True)
    sh("Items", ["code", "description", "category", "type", "unit", "note"], [(r["code"], r["descr"], "waterproofing works", "work", "", "") for r in c.execute("select code,descr from items order by rowid")])
    sh("Materials", ["code", "name", "category", "unit", "synonyms"], [tuple(r) for r in c.execute("select code,name,category,unit,synonyms from materials")])
    sh("Stock", ["site", "store", "material_code", "qty_on_hand", "unit", "reserved", "last_updated", "source"], [tuple(r) for r in c.execute("select site,store,material_code,qty,unit,reserved,updated,source from stock")])
    sh("Usage", ["date", "site", "where_used", "material_code", "qty", "unit", "work_order", "source"], [tuple(r) for r in c.execute("select used_on,site,location,material_code,qty,unit,work_order,source from usage")])
    sh("Vendors", ["name", "category", "registered", "test_email", "category_source"], [(r["name"], r["category"], "yes" if r["registered"] else "no", r["test_email"], r["category_source"]) for r in c.execute("select * from vendors")])
    sh("Vendor_history", ["vendor", "project", "work", "quality_1_5", "on_time_1_0", "delay_days", "dispute_1_0", "note", "source", "job_date"],
       [(r["name"], r["project"], r["work"], r["quality"], r["on_time"], r["delay_days"], r["disputes"], r["note"], r["source"], r["job_date"]) for r in c.execute("select h.*, v.name from vendor_history h join vendors v on v.key=h.vendor_key")])
    sh("Vendor_rounds", ["vendor", "responded_1_0", "hours_to_reply", "chased_1_0", "open_questions", "price_vs_lowest_pct", "source"],
       [(r["name"], r["responded"], r["hours"], r["chased"], r["issues"], r["vs_lowest_pct"], r["source"]) for r in c.execute("select s.*, v.name from vendor_signals s join vendors v on v.key=s.vendor_key")])
    sh("Synonyms", ["word", "maps_to"], [tuple(r) for r in c.execute("select word,maps_to from synonyms")])
    sh("Stock_transfer_requests", ["case", "from_site", "to_site", "material_code", "qty", "unit", "status", "when"], [tuple(r) for r in c.execute("select case_id,from_site,to_site,material_code,qty,unit,status,ts from stock_moves")])
    c.close(); wb.save(path)
