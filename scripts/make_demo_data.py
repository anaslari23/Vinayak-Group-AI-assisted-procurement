"""Generate the SYNTHETIC demo package: tender, item master, vendor list, history, 12 quote files in 4 layouts, and ground truth.
Everything here is invented; no Vinayak data. Run: python scripts/make_demo_data.py  (then scripts/extract_demo.py)"""
import json, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent / "data" / "demo"
(ROOT / "quotes").mkdir(parents=True, exist_ok=True); (ROOT / "eval").mkdir(exist_ok=True)
SQM = 0.09290304
LINES = [(1, "Waterproofing at toilet floor", "SQFT", 9850.00, "WPD.01"), (2, "Waterproofing at toilet wall (up to 450 mm height)", "SQFT", 8420.50, "WPD.02"),
         (3, "Waterproofing on toilet depression channel", "RMT", 380.0, "WPD.03"), (4, "Waterproofing at kitchen & wash area, floor", "SQFT", 1320.75, "WPD.04"),
         (5, "Waterproofing at kitchen & wash area, wall", "SQFT", 1410.20, "WPD.05"), (6, "Waterproofing at balcony & terrace, floor", "SQFT", 6230.40, "WPD.06"),
         (7, "Waterproofing at balcony & terrace, wall", "SQFT", 1785.90, "WPD.07")]
TENDER = {"id": "DEMO-TB-WP", "project": "Demo Heights", "package": "Waterproofing, Tower-B", "synthetic": True,
          "lines": [{"sl": a, "title": b, "unit": c, "qty": d, "item_code": e} for a, b, c, d, e in LINES],
          "standard_terms": ["PF & ESI registration is mandatory.", "TDS will be deducted as per Government norms.", "5% security deposit is released 12 months after completion.",
                             "GST will be paid extra, on the invoice.", "Water and electricity at one point, and a labour hutment, are provided by the developer.",
                             "Safety PPE is the contractor's cost."]}
(ROOT / "tender.json").write_text(json.dumps(TENDER, indent=1))

# ---------- synthetic ERP item master: invented wording, but it carries the same KINDS of mess a real ERP export has
# (exact repeats, one code with two meanings, one work under many codes, look-alike codes, typos, a very long description, a
# code whose meaning differs from the site sheet). 'WPD' is the demo prefix.
LONG = ("WATERPROOFING WORKS {BALCONY WALL (UPTO 200 MM HIGHT)} Providing and applying integral multi-layer waterproofing treatment on vertical and "
        "horizontal surfaces of depressed portions of W.C., bathrooms, kitchens, balconies, stilts, etc., including surface preparation, cleaning, "
        "sealing of all junctions, joints, corners and pipe penetrations, consisting of: (a) first course of cement slurry mixed with an approved integral "
        "waterproofing compound, (b) second course of cement plaster mixed with waterproofing compound, including rounding off the junction, "
        "(c) third course of polymer modified slurry, (d) fourth course of protective cement plaster, including curing, scaffolding, labour, tools and "
        "tackles, making good adjoining surfaces and ensuring watertightness, as directed by the Engineer-in-Charge.") * 1
rows = [
 ("WPD.UGR.A", "UGR OUTSIDE WALL AREA-METHODOLOGY-1"), ("WPD.UGR.B", "UGR OUTSIDE WALL AREA-METHODOLOGY-2"), ("WPD.UGR.C", "UGR INSIDE FLOOR & WALL AREA"),
 ("WPD.STP.A", "STP OUTSIDE WALL AREA"), ("WPD.STP.B", "STP INSIDE WALL & FLOOR AREA"), ("STPD_UGR", "Nozzle grouting at all construction joints"),
 ("STPD_UGR_01", "Neat Cement Punning Work"), ("W.PD", "WATER PROOFING WORK (UGR, STP & LIFT PIT)"), ("W.PD.01", "Water Proofing LIFT PIT/UGR/STP/WALL"),
 ("W.PD.02", "Waterproofing work in Lift Pit/UGR/STP floor"), ("W.PD.03", "Grouting at all construction joints"), ("W.PD.04", "Neat Cement Punning Work"),
 ("W.PD.05", "Screed Concrete Work"), ("W.PD-.1", "REPAIRING WORK FOR WATERPROOFING"),
 ("WPD.01.A", "WATERPROOFING AT TOILET FLOOR-A"), ("WPD.02.A", "WATERPROOFING AT TOILET WALL (UPTO 450 MM HIGHT)-A"), ("WPD.04.A", "WATERPROOFING AT KITCHEN FLOOR-A"),
 ("WPD.05.A", "WATERPROOFING AT KITCHEN WALL-A"), ("WPD.06.A", "WATERPROOFING AT BALCONY FLOOR-A"), ("WPD.07.A", LONG), ("WPD.08.A", "WATERPROOFING AT OT FLOOR & WALL"),
 ("WPD.01.B", "WATERPROOFING AT TOILET FLOOR-B"), ("WPD.02.B", "WATERPROOFING AT TOILET WALL (UPTO 450 MM HIGHT)-B"), ("WPD.04.B", "WATERPROOFING AT KITCHEN FLOOR-B"),
 ("WPD.05.B", "WATERPROOFING AT KITCHEN WALL-B"), ("WPD.06.B", "WATERPROOFING AT BALCONY FLOOR-B"), ("WPD.07.B", "WATERPROOFING WORKS {BALCONY WALL (UPTO 200 MM HIGHT)}"),
 ("WPD.08.B", "WATERPROOFING AT OT FLOOR & WALL"), ("WPD.09.B", "WATERPROOFING AT ROOF & MUMPTY TOP FLOOR & WALL"), ("WPD.COBA", "WATERPROOFING WITH BRICK BAT COBA"),
 ("WPD.10.B", "OWHT TANK FLOOR & WALL WATERPROOFING"), ("WPD.11.B", "*NOZZLE GROUTING (IF REQUIRED)"), ("WPD.11.B_", "NOZZLE GROUTING (*IF REQUIRED)"),
 ("WPD.EXP.01", "Expansion Joint Polysulphide Treatment"), ("W.PD.08", "75 MM SCREED CONCRETE WORK ABOVE ROOF"), ("WPD.RC", "CONCRETE PUMPING WORK (SCREED CONCRETE)"),
 ("WPD.RC.1", "CONCRETE PUMP CHARGES-B"), ("WPD.11.B", "*NOZZLE GROUTING (IF REQUIRED)"), ("W.PD.11B", "Epoxy Tar Based Coating for S.T.P"),
 ("W.PD.01", "Water Proofing LIFT PIT/UGR/STP/WALL"), ("W.PD.02", "Waterproofing work in Lift Pit/UGR/STP floor"), ("W.PD.03", "Grouting at all construction joints"),
 ("W.PD.04", "Neat Cement Punning Work"), ("W.PD.05", "Screed Concrete Work"),
 ("WPD.01", "WATERPROOFING AT TOILET FLOOR"), ("WPD.02", "WATERPROOFING AT TOILET WALL (UPTO 450 MM HIGHT)"),
 ("WPD.03", "SUPPLY & APPLICATION OF WATERPROOFING COMPOUND ON TOILET DEPRESSION CHANNEL"), ("WPD.04", "WATERPROOFING AT KITCHEN FLOOR"), ("WPD.05", "WATERPROOFING AT KITCHEN WALL"),
 ("WPD.06", "WATERPROOFING AT BALCONY FLOOR"), ("WPD.07", "WATERPROOFING AT BALCONY WALL (UPTO 200 MM HIGHT)"), ("WPD.08", "PRESSURE GROUTING INSIDE R.C.C FLOOR & WALL"),
 ("WPD.09", "SCREED CONCRETE IN FLOOR (TOILET & BALCONY)"),
 ("WPD.01.A", "WATERPROOFING AT TOILET FLOOR-A"), ("WPD.04.B", "WATERPROOFING AT KITCHEN WALL-B"), ("WPD.05.B", "WATERPROOFING AT BALCONY FLOOR-B"),
 ("WPD.06.B", "WATERPROOFINWORKS {BALCONY WALL (UPTO 200 MM HIGHT)}"), ("WPD.07.B", "WATERPROOFING AT OT FLOOR & WALL"),
 ("WPD.EXP.01", "Hypalon Expansion Joint Treatment"), ("WPD.RC1", "CONCRETE PUMPING WORK (SCREED CONCRETE)-1"), ("WPD.GR.01", "EXPENSION JOINT ALUMINIUM SHEET FIXING"),
 ("WPD.GR.02", "EXTRA FOR 300 MM COMBIFLEX TAPE SUPPLY & FIXING"), ("WPD.GR.03", "PROVIDING & APPLYING SEALENT AT EXPENSION JOINT TILES GAP"), ("WPD.MR", "Scaffolding charges"),
 ("WPD.12.C", "75 MM SCREED CONCRETE WORK ABOVE ROOF"), ("WPD.11.B", "*NOZZLE GROUTING (IF REQUIRED)")]
items = [{"code": c, "desc": d} for c, d in rows]
(ROOT / "items.json").write_text(json.dumps(items, indent=1))
# site sheet (what the engineers' BOQ calls things). Note WPD.08 = nozzle grouting on the site sheet but pressure grouting in the ERP.
site = [{"sl": l[0], "wo_code": l[4], "desc": l[1].upper(), "uom": l[2]} for l in LINES] + [{"sl": 8, "wo_code": "WPD.08", "desc": "NOZZLE GROUTING (IF REQUIRED)", "uom": "NOS"}]
(ROOT / "site_boq.json").write_text(json.dumps(site, indent=1))
(ROOT / "item_meta.json").write_text(json.dumps({"prefix": "WPD", "aliases": {
  "toilet_floor": ["WPD.01"], "toilet_floor_family": ["WPD.01", "WPD.01.A", "WPD.01.B"], "toilet_wall_family": ["WPD.02", "WPD.02.A", "WPD.02.B"],
  "balcony_wall_family": ["WPD.07", "WPD.07.A", "WPD.07.B"], "nozzle": ["WPD.11.B", "WPD.11.B_"], "kitchen_floor_B": ["WPD.04.B", "WPD.03.B"],
  "punning": ["STPD_UGR_01", "W.PD.04"], "expansion": ["WPD.EXP.01"], "pump": ["WPD.RC", "WPD.RC1", "WPD.RC.1"], "roof": ["WPD.09.B"],
  "kitchen_floor_family": ["WPD.04", "WPD.04.A", "WPD.04.B"], "kitchen_wall_family": ["WPD.05", "WPD.05.A", "WPD.05.B"], "balcony_floor_family": ["WPD.06", "WPD.06.A", "WPD.06.B"],
  "ot_family": ["WPD.08.A", "WPD.08.B", "WPD.07.B"], "pressure_grout": ["WPD.08"], "screed_roof": ["W.PD.08", "WPD.12.C"], "depression": ["WPD.03"]}}, indent=1))
(ROOT / "synonyms.json").write_text(json.dumps({"bathroom": "toilet", "bath": "toilet", "washroom": "toilet", "wc": "toilet", "chhat": "roof", "chat": "roof", "terrace": "roof",
  "sink": "kitchen", "rooftop": "roof", "tank": "tank", "balkoni": "balcony", "pani": "water"}, indent=1))

# ---------- synthetic vendors
HEAD = ["Kalyani Seal & Shield Pvt. Ltd.", "Rupashi Waterproofing Works", "Ganga Damp-Proof Contractors", "Teesta Prevention & Cure Co."]
others = ["Aqua Barrier Agencies", "Neer Chemicals Kolkata", "Sundarban Civil Works", "Bharat Rice Mill Machinery", "Lotus Electricals Pvt. Ltd.", "Hooghly Interiors", "Metro Lift Services", "Ananda Furniture Mart",
          "Sagar Piling Solutions", "Dolphin Fire Safety", "Eastern Glazing Co.", "Pushpak Solar Tech", "Arambagh Concrete Pvt. Ltd.", "Nabadwip Paver & Kerb Works", "Sonar Bangla Paints", "Mohana Plumbing Services", "Sree Ganesh Enterprise",
          "Rajdhani Aluminium", "Tapan Kumar Das", "Bidhan Road Builders"]
(ROOT / "contractors.json").write_text(json.dumps([HEAD[0], HEAD[1]] + others, indent=1))
(ROOT / "vendor_meta.json").write_text(json.dumps({"unregistered": [HEAD[2], HEAD[3]], "wp_hints": ["KALYANI", "RUPASHI", "AQUA BARRIER", "NEER CHEMICALS"], "road_hints": ["NABADWIP", "BIDHAN", "ARAMBAGH"]}, indent=1))
(ROOT / "history.json").write_text(json.dumps({"KALYANI": [[5, 1, 0, 0, "Tower-A toilets, finish very good"], [4, 1, 0, 0, "Podium waterproofing"], [5, 1, 4, 0, "Terrace, 4 days late (rain)"]],
                                               "RUPASHI": [[3, 0, 22, 1, "Leakage callbacks in 2 flats"], [4, 1, 0, 0, "Balcony work"], [3, 0, 15, 1, "Measurement dispute on final bill"]]}, indent=1))

# ---------- quote specs. rate list is per vendor unit.
def spec(vendor, key, layout, date, validity, gst, adv, credit, warranty, start, freight, units, qty, rate, supplies=(), pay_text=None, note=None):
    return dict(vendor=vendor, key=key, layout=layout, date=date, validity=validity, gst=gst, adv=adv, credit=credit, warranty=warranty, start=start,
                freight=freight, units=units, qty=qty, rate=rate, supplies=list(supplies), pay_text=pay_text, note=note)
TQ = [l[3] for l in LINES]; U = [l[2] for l in LINES]
OLDQ = [20400, 17500, 790, 2750, 2930, 12960, 3710]
SUP = ["cement", "sand", "scaffolding", "water and electricity"]
HEADSPECS = [
    spec(HEAD[0], "KALYANI", "table", "2026-11-12", 60, (18, "extra"), 30, 10, 5, 12, None, U, TQ, [40, 40, 42, 40, 40, 40, 40], SUP),
    spec(HEAD[1], "RUPASHI", "handwritten", "2026-10-14", None, (None, "unstated"), None, None, None, None, None, U, OLDQ, [33, 33, 48, 31, 31, 33, 33], SUP[:2]),
    spec(HEAD[2], "GANGA", "letterhead", "2026-08-20", 30, (18, "extra"), 25, 30, 10, 14, None, ["SQM", "SQM", "RMT", "SQM", "SQM", "SQM", "SQM"],
         [round(q * SQM, 2) if u == "SQFT" else q for q, u in zip(OLDQ, U)], [410, 410, 62, 395, 395, 410, 410], SUP),
    spec(HEAD[3], "TEESTA", "email", None, None, (18, "inclusive"), None, None, None, None, 15000, U, OLDQ, [39, 39, 80, 36, 36, 39, 39], SUP[:3], pay_text="Payment terms to be discussed."),
]
rng = random.Random(7)
NAMES = ["Meghna Seal Works", "Hooghly Coatings", "Padma Proofing Co.", "Damodar Chemical Applicators", "Subarna Contractors", "Torsa Protective Coatings", "Mayurakshi Builders & Waterproofers", "Ajay Aquaseal Agency"]
EXTRA = []
for i, nm in enumerate(NAMES):
    sq = rng.random() < 0.4
    units = ["SQM" if (sq and u == "SQFT") else u for u in U]
    base_rate = rng.choice([32, 36, 41, 45, 50])
    rates = [round(base_rate * (10.7639 if (units[j] == "SQM") else 1) * (1.4 if U[j] == "RMT" else 1), 2) if U[j] == "RMT" or units[j] == "SQM" else base_rate + rng.choice([0, 0, 2, -2]) for j in range(7)]
    rates = [round(r) for r in rates]
    qty = [round(q * SQM, 2) if units[j] == "SQM" else q for j, q in enumerate(TQ)]
    gst = rng.choice([(18, "extra"), (18, "inclusive"), (18, "extra"), (None, "unstated")])
    EXTRA.append(spec(nm, nm.split()[0].upper(), ["table", "letterhead", "email", "table"][i % 4], rng.choice(["2026-11-03", "2026-11-09", "2026-10-28", "2026-11-15"]), rng.choice([30, 45, 60, None]),
                      gst, rng.choice([None, 10, 20, 30]), rng.choice([None, 15, 21, 30, 45]), rng.choice([None, 3, 5, 7]), rng.choice([None, 7, 10, 15]), rng.choice([None, None, 8000, 12000]),
                      units, qty, rates, rng.sample(SUP, 3)))

# ---------- ground truth (independent of the app's code)
def truth(s):
    base = 0.0
    for j in range(7):
        r = s["rate"][j] * (SQM if (s["units"][j] == "SQM" and U[j] == "SQFT") else 1)
        base += r * TQ[j]
    if s["gst"][1] == "inclusive": base /= 1 + s["gst"][0] / 100
    base += s["freight"] or 0
    return dict(date=s["date"], validity=s["validity"], gst=list(s["gst"]), adv=s["adv"], credit=s["credit"], warranty=s["warranty"], start=s["start"], freight=s["freight"],
                units=s["units"], qty=s["qty"], rate=s["rate"], base=round(base))
TRUTH = {s["key"]: truth(s) for s in HEADSPECS + EXTRA}
(ROOT / "truth.json").write_text(json.dumps(TRUTH, indent=1))

# ---------- renderers
st = getSampleStyleSheet()
def terms_lines(s):
    t = []
    g = s["gst"]
    if g[1] == "extra": t.append(f"GST @ {g[0]}% extra as applicable.")
    elif g[1] == "inclusive": t.append(f"Our rates are inclusive of GST @ {g[0]}%.")
    if s["pay_text"]: t.append(s["pay_text"])
    else:
        if s["adv"]: t.append(f"{s['adv']}% advance along with the work order.")
        if s["credit"]: t.append(f"Running bills payable within {s['credit']} days of submission.")
    if s["start"]: t.append(f"Work can start within {s['start']} days of the order.")
    if s["validity"]: t.append(f"This offer is valid for {s['validity']} days from the date of quotation.")
    if s["warranty"]: t.append(f"{s['warranty']} year warranty on the application.")
    if s["freight"]: t.append(f"Mobilisation / freight Rs {s['freight']:,} lump sum extra.")
    if s["supplies"]: t.append("Client to provide free of cost: " + ", ".join(s["supplies"]) + ".")
    return t

def title(j): return LINES[j][1].upper()
def dmy(d): return d[8:] + "." + d[5:7] + "." + d[:4] if d else ""

def render_table(s, path):
    doc = SimpleDocTemplate(str(path), pagesize=A4); el = [Paragraph(f"<b>{s['vendor']}</b>", st["Title"]), Paragraph(f"QUOTATION - {TENDER['project']} ({TENDER['package']})  {dmy(s['date'])}", st["Normal"]), Spacer(1, 10)]
    rows = [["SL", "ITEM", "UNIT", "QTY", "RATE", "AMOUNT"]] + [[j + 1, Paragraph(title(j), st["BodyText"]), s["units"][j], f"{s['qty'][j]:,.2f}", f"{s['rate'][j]:,.2f}", f"{s['qty'][j] * s['rate'][j]:,.2f}"] for j in range(7)]
    t = Table(rows, colWidths=[24, 230, 40, 70, 50, 80]); t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    el += [t, Spacer(1, 12), Paragraph("<b>Terms &amp; conditions</b>", st["Normal"])] + [Paragraph(f"{i}. {x}", st["BodyText"]) for i, x in enumerate(terms_lines(s), 1)]
    doc.build(el)

def render_letterhead(s, path):
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    el = [Paragraph(f"<b>{s['vendor'].upper()}</b>", st["Title"]), Paragraph("12 Demo Lane, Kolkata 700000 | GSTIN 19AAAAA0000A1Z5 (demo)", st["Normal"]), Spacer(1, 8),
          Paragraph(f"Ref: Q/{s['key'][:3]}/26-27 &nbsp;&nbsp; Date: {dmy(s['date'])}", st["Normal"]), Paragraph(f"To: Demo Heights developer, Kolkata. Sub: {TENDER['package']}", st["Normal"]), Spacer(1, 8)]
    for j in range(7):
        el.append(Paragraph(f"<b>{j+1}. {title(j)}</b> - {s['qty'][j]:,.2f} {s['units'][j]} @ Rs {s['rate'][j]:,.2f} per {s['units'][j]}. Supply and apply surface preparation, primer, two coats of the approved polymer-modified coating, curing and protective plaster as per specification.", st["BodyText"]))
    el += [Spacer(1, 8), Paragraph("<b>Terms &amp; Conditions:</b>", st["Normal"])] + [Paragraph(f"{i}. {x}", st["BodyText"]) for i, x in enumerate(terms_lines(s), 1)] + [Paragraph("Yours truly,", st["Normal"])]
    doc.build(el)

def render_email(s, path):
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    d = s["date"]
    el = [Paragraph(f"From: {s['vendor']}", st["Normal"]), Paragraph(f"Subject: Re: SEEKING QUOTATION FOR WATERPROOFING WORK AT {TENDER['project'].upper()}", st["Normal"])]
    if d: el.append(Paragraph(f"Date: {dmy(d)}", st["Normal"]))
    el += [Spacer(1, 8), Paragraph("Dear Sir, thank you for the enquiry. Our rates against your quantities are below:", st["BodyText"])]
    for j in range(7): el.append(Paragraph(f"Item {j+1} - {LINES[j][1]}: {s['qty'][j]:,.2f} {s['units'][j]} x Rs {s['rate'][j]:,.2f}", st["BodyText"]))
    el += [Spacer(1, 6)] + [Paragraph(x, st["BodyText"]) for x in terms_lines(s)] + [Paragraph("Regards", st["Normal"])]
    doc.build(el)

def render_handwritten(s, path):
    W, H = 1240, 1754; img = Image.new("RGB", (W, H), (250, 249, 245)); d = ImageDraw.Draw(img)
    f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 26); fb = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 34)
    hw = ImageFont.truetype("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf", 44)
    d.text((60, 50), s["vendor"].upper(), font=fb, fill=(40, 60, 140)); d.text((60, 100), "12 Demo Lane, Kolkata 700000   Ph 90000 00000 (demo)", font=f, fill=(60, 60, 60))
    d.text((60, 150), f"WATERPROOFING WORK AT {TENDER['project'].upper()}      {dmy(s['date'])}", font=f, fill=(0, 0, 0))
    x = [60, 130, 760, 870, 1010]; y = 230
    for xx, t in zip(x, ["SL", "ITEM", "UNIT", "QUANTITY", "RATE"]): d.text((xx, y), t, font=f, fill=(0, 0, 0))
    d.line([(50, y + 40), (1190, y + 40)], fill=(0, 0, 0), width=2)
    y += 60
    for j in range(7):
        d.text((x[0], y), str(j + 1), font=f, fill=(0, 0, 0)); d.text((x[1], y), title(j)[:36], font=f, fill=(0, 0, 0)); d.text((x[2], y), s["units"][j], font=f, fill=(0, 0, 0)); d.text((x[3], y), f"{s['qty'][j]:,.2f}", font=f, fill=(0, 0, 0))
        d.text((x[4] + (j % 3) * 4, y - 12 + (j % 2) * 3), f"{s['rate'][j]:g}/-", font=hw, fill=(20, 30, 120)); d.line([(50, y + 50), (1190, y + 50)], fill=(160, 160, 160), width=1); y += 90
    d.ellipse([(850, 1000), (1100, 1150)], outline=(150, 30, 60), width=4); d.text((890, 1060), s["vendor"].split()[0].upper(), font=f, fill=(150, 30, 60))
    img = img.rotate(-0.7, expand=False, fillcolor=(250, 249, 245)); img.save(path, "PDF", resolution=150)

R = {"table": render_table, "letterhead": render_letterhead, "email": render_email, "handwritten": render_handwritten}
for s in HEADSPECS: R[s["layout"]](s, ROOT / "quotes" / f"quote_{s['key'].lower()}.pdf")
for s in EXTRA: R[s["layout"]](s, ROOT / "eval" / f"quote_{s['key'].lower()}.pdf")
print("demo package written:", len(HEADSPECS), "headline quotes,", len(EXTRA), "eval quotes")
for s in HEADSPECS: print(s["key"], "base", TRUTH[s["key"]]["base"])
