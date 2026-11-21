"""Build the master workbook (data_dir/vinayak_master.xlsx) from a data dir's JSON files plus SYNTHETIC stock, usage and sites.
The workbook is the editable source of truth the agent reads. Run: python scripts/make_master_xlsx.py [data_dir]   (default data/demo)"""
import json, random, sys
from datetime import date, timedelta
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

D = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "demo"
rd = lambda n, default=None: json.loads((D / n).read_text()) if (D / n).exists() else default
SYN = "SYNTHETIC DEMO DATA"
rng = random.Random(11)

items = rd("items.json", [])
contractors = rd("contractors.json", [])
meta = rd("vendor_meta.json", {"unregistered": [], "wp_hints": [], "road_hints": []})
history = rd("history.json", {}); signals = rd("signals.json", {}); synonyms = rd("synonyms.json", {})

SITES = [("Demo Heights", "Demo Heights", "Kolkata", "Towers A and B, finishing stage"), ("Demo Greens", "Demo Greens", "Kolkata", "Internal roads and landscape"),
         ("Demo Villas", "Demo Villas", "Kolkata", "Villa plots, structure stage")]
MATERIALS = [("MAT.CEM.01", "Portland cement OPC 53 grade", "cement", "bag", "cement, opc, opc 53, cement bag"), ("MAT.SND.01", "River sand", "aggregate", "cum", "sand, river sand, balu"),
             ("MAT.SD.01", "Stone dust", "aggregate", "cum", "stone dust, dust"), ("MAT.TMT.12", "TMT bar 12 mm Fe500D", "steel", "kg", "tmt, steel, rebar, tmt 12, iron rod"),
             ("MAT.TMT.16", "TMT bar 16 mm Fe500D", "steel", "kg", "tmt 16, steel 16"), ("MAT.PAV.60", "Paver block 60 mm", "road", "sqm", "paver, paver block, paver 60"),
             ("MAT.KRB.01", "Kerb stone M8 150x300x600", "road", "nos", "kerb, kerb stone"), ("MAT.BRK.01", "Brick bats (jhama)", "aggregate", "cft", "brick bats, jhama, brick bat"),
             ("MAT.WPC.01", "Cementitious waterproofing coat, 20 kg pack", "waterproofing", "pack", "waterproofing compound, brushbond, wp coat"), ("MAT.SBR.01", "SBR bonding agent", "waterproofing", "ltr", "sbr, bonding agent"),
             ("MAT.STU.01", "Stucco plaster mix", "waterproofing", "bag", "stucco, plaster mix"), ("MAT.SCF.01", "Scaffolding set (frame and board)", "equipment", "nos", "scaffolding, scaffold, staging")]
# (site, store, code, qty, reserved)
STOCK = [("Demo Heights", "Main store", "MAT.CEM.01", 340, 40), ("Demo Heights", "Main store", "MAT.SND.01", 28, 5), ("Demo Heights", "Main store", "MAT.STU.01", 60, 0),
         ("Demo Heights", "Tower-B store", "MAT.SCF.01", 85, 20), ("Demo Heights", "Tower-B store", "MAT.WPC.01", 22, 0), ("Demo Heights", "Main store", "MAT.TMT.12", 1800, 0),
         ("Demo Greens", "Road yard", "MAT.CEM.01", 120, 0), ("Demo Greens", "Road yard", "MAT.PAV.60", 400, 100), ("Demo Greens", "Road yard", "MAT.KRB.01", 150, 0),
         ("Demo Greens", "Road yard", "MAT.SD.01", 40, 0), ("Demo Greens", "Road yard", "MAT.BRK.01", 900, 0), ("Demo Villas", "Plot store", "MAT.CEM.01", 80, 0),
         ("Demo Villas", "Plot store", "MAT.TMT.12", 2600, 400), ("Demo Villas", "Plot store", "MAT.TMT.16", 1200, 0), ("Demo Villas", "Plot store", "MAT.SND.01", 12, 0)]
LOCS = {"Demo Heights": ["Tower-B floor 5 toilets", "Tower-B floor 6 toilets", "Tower-A podium", "Tower-B terrace"], "Demo Greens": ["Road stretch A", "Road stretch B", "Gate plaza"], "Demo Villas": ["Plot 12 foundation", "Plot 14 columns", "Plot 15 slab"]}
today = date(2026, 11, 21)
USAGE = []
for site, store, code, qty, res in STOCK:
    unit = next(m[3] for m in MATERIALS if m[0] == code)
    for k in range(rng.randint(1, 3)):
        d = today - timedelta(days=rng.randint(2, 40)); u = max(1, round(qty * rng.uniform(0.03, 0.12), 1))
        USAGE.append((d.isoformat(), site, rng.choice(LOCS[site]), code, u, unit, f"WO-{rng.randint(100, 480)}"))
USAGE.sort()

wb = Workbook(); head = PatternFill("solid", fgColor="E8E2D5"); bold = Font(bold=True)
def sheet(name, cols, rows, widths=None, first=False):
    ws = wb.active if first else wb.create_sheet(); ws.title = name; ws.append(cols)
    for c in ws[1]: c.fill, c.font = head, bold
    for r in rows: ws.append(list(r))
    for i, c in enumerate(cols, 1): ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(c, max(14, len(c) + 4))
    ws.freeze_panes = "A2"; return ws

ws = wb.active; ws.title = "README"
for line in ["Vinayak procurement agent: master workbook", "",
             "This workbook is the editable source of truth the agent reads. Edit it, upload it on the Master page (or replace the file), and the agent reloads it.",
             "Sheets: Sites, Items (ERP work-item list), Materials (stock catalogue), Stock (what is held where), Usage (what was used where), Vendors, Vendor_history, Vendor_rounds, Synonyms.",
             "Keep the header row exactly as is. Item codes in Stock and Usage must exist in Materials; sites must exist in Sites.",
             "Vendor test_email must end with @rfq-test.example. Any other address is ignored: the agent never stores or mails a real vendor address.",
             "API keys do NOT belong in this file. They live in the server's environment only.",
             f"Rows tagged '{SYN}' are invented for the demo. Replace them with real data."]:
    ws.append([line])
ws["A1"].font = Font(bold=True, size=14); ws.column_dimensions["A"].width = 130
sheet("Sites", ["site", "project", "city", "note"], SITES)
sheet("Items", ["code", "description", "category", "type", "unit", "note"], [(i["code"], i["desc"], "waterproofing works", "work", "", "") for i in items], {"description": 70})
sheet("Materials", ["code", "name", "category", "unit", "synonyms"], MATERIALS, {"name": 40, "synonyms": 40})
sheet("Stock", ["site", "store", "material_code", "qty_on_hand", "unit", "reserved", "last_updated", "source"],
      [(s, st, c, q, next(m[3] for m in MATERIALS if m[0] == c), r, (today - timedelta(days=rng.randint(0, 6))).isoformat(), SYN) for s, st, c, q, r in STOCK])
sheet("Usage", ["date", "site", "where_used", "material_code", "qty", "unit", "work_order", "source"], [(*u, SYN) for u in USAGE], {"where_used": 28})
vend = []
for n in contractors:
    cat = "waterproofing" if any(h in n.upper() for h in meta["wp_hints"]) else ("road/civil" if any(h in n.upper() for h in meta["road_hints"]) else "other")
    vend.append((n, cat, "yes", "", "guessed from name" if cat != "other" else ""))
for n in meta["unregistered"]: vend.append((n, "waterproofing", "no", "", "from their quotation"))
sheet("Vendors", ["name", "category", "registered", "test_email", "category_source"], vend, {"name": 44})
hist_rows = []
for frag, rows in history.items():
    nm = next((n for n in contractors if frag in n.upper()), frag)
    for i, (q, ot, dd, ds, note) in enumerate(rows):
        hist_rows.append((nm, "past project", "waterproofing", q, ot, dd, ds, note, SYN, ["2025-04-10", "2025-10-05", "2026-05-20", "2026-08-15"][i % 4]))
sheet("Vendor_history", ["vendor", "project", "work", "quality_1_5", "on_time_1_0", "delay_days", "dispute_1_0", "note", "source", "job_date"], hist_rows, {"note": 40})
rounds = []
for frag, rows in signals.items():
    nm = next((n for n in contractors if frag in n.upper()), frag)
    for resp, hrs, ch, iss, vs in rows: rounds.append((nm, resp, hrs, ch, iss, vs, SYN))
sheet("Vendor_rounds", ["vendor", "responded_1_0", "hours_to_reply", "chased_1_0", "open_questions", "price_vs_lowest_pct", "source"], rounds)
sheet("Synonyms", ["word", "maps_to"], list(synonyms.items()))
out = D / "vinayak_master.xlsx"; wb.save(out); print("wrote", out, "|", len(items), "items,", len(STOCK), "stock rows,", len(USAGE), "usage rows,", len(vend), "vendors")
