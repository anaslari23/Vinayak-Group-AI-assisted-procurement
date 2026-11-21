import json, os, sqlite3
from pathlib import Path
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "app.db"))
from .tender import DATA_DIR as SEED

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(code TEXT, descr TEXT, source TEXT DEFAULT 'ERP export (data pack)');
CREATE TABLE IF NOT EXISTS vendors(key TEXT PRIMARY KEY, name TEXT, category TEXT, registered INT, test_email TEXT, category_source TEXT);
CREATE TABLE IF NOT EXISTS vendor_history(id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_key TEXT, project TEXT, work TEXT,
   quality INT, on_time INT, delay_days INT, disputes INT, note TEXT, source TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS requirements(id INTEGER PRIMARY KEY AUTOINCREMENT, raw TEXT, channel TEXT, parsed TEXT, match TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mail_log(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, to_addr TEXT, subject TEXT, body TEXT, status TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS quotes(case_id INTEGER DEFAULT 0, vendor_key TEXT, source_file TEXT, extracted TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(case_id, vendor_key));
CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT, waiting TEXT, data TEXT, clock_offset_h REAL DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, actor TEXT, kind TEXT, text TEXT, payload TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, to_role TEXT, text TEXT, buttons TEXT, sent INT DEFAULT 0, ts TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS inbox_seen(id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, role TEXT, case_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, tender TEXT, decision TEXT, note TEXT, recommended TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
"""

def _meta():
    f = SEED / "vendor_meta.json"
    return json.loads(f.read_text()) if f.exists() else {"unregistered": [], "wp_hints": [], "road_hints": []}


def _history():
    f = SEED / "history.json"
    return json.loads(f.read_text()) if f.exists() else {}


def con():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
    return c


def vkey(name: str) -> str:
    import re
    n = re.sub(r"[^a-z0-9 ]", "", name.lower())
    for w in ("pvt", "ltd", "private", "limited", "sales", "services", "construction waterproofing", "prevention cure co"):
        pass
    return "-".join(n.split()[:2])


def init(reset=False):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    if reset and Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
    c = con(); c.executescript(SCHEMA)
    if c.execute("select count(*) from items").fetchone()[0] == 0 and (SEED / "items.json").exists():
        for it in json.loads((SEED / "items.json").read_text()):
            c.execute("insert into items(code,descr) values(?,?)", (it["code"], it["desc"]))
    if c.execute("select count(*) from vendors").fetchone()[0] == 0:
        names = json.loads((SEED / "contractors.json").read_text()) if (SEED / "contractors.json").exists() else []
        for n in names:
            k = vkey(n)
            m = _meta()
            cat = "waterproofing" if any(h in n.upper() for h in m["wp_hints"]) else ("road/civil" if any(h in n.upper() for h in m["road_hints"]) else "other")
            c.execute("insert or ignore into vendors values(?,?,?,?,?,?)", (k, n, cat, 1, f"rfq+{k}@rfq-test.example", "guessed from name"))
        # the four bidders: add the two that are NOT in Vinayak's contractor list as unregistered
        for n in _meta()["unregistered"]:
            k = vkey(n); c.execute("insert or ignore into vendors values(?,?,?,?,?,?)", (k, n, "waterproofing", 0, f"rfq+{k}@rfq-test.example", "from their quotation"))
        for frag, rows in _history().items():
            row = c.execute("select key from vendors where upper(name) like ?", (f"%{frag}%",)).fetchone()
            k = row["key"]
            for q, ot, dd, ds, note in rows:
                c.execute("insert into vendor_history(vendor_key,project,work,quality,on_time,delay_days,disputes,note,source) values(?,?,?,?,?,?,?,?,?)",
                          (k, "past project", "waterproofing", q, ot, dd, ds, note, "SYNTHETIC DEMO DATA"))
    c.commit(); c.close()


def match_vendor(name: str):
    """Map a name found on a quote to a vendor row (fuzzy). Returns (row|None, score)."""
    c = con(); rows = c.execute("select * from vendors").fetchall(); c.close()
    best, bs = None, 0
    for r in rows:
        s = fuzz.token_set_ratio(name.lower(), r["name"].lower())
        if s > bs: best, bs = r, s
    return (best, bs) if bs >= 80 else (None, bs)


def history(vendor_key: str):
    c = con(); rows = [dict(r) for r in c.execute("select * from vendor_history where vendor_key=?", (vendor_key,))]; c.close()
    return rows
