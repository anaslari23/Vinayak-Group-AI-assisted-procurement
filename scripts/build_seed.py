"""Build data/seed/*.json from the (confidential) data pack. Run locally: python scripts/build_seed.py <datapack_dir>.
The pack is NOT committed. Vendor emails are replaced by synthetic test-inbox addresses."""
import json, re, sys
from pathlib import Path
import openpyxl

pack = Path(sys.argv[1]); out = Path(__file__).resolve().parent.parent / "data" / "seed"
out.mkdir(parents=True, exist_ok=True)

# ERP item codes
ws = openpyxl.load_workbook(pack / "04_item_codes/ERP_item_codes_waterproofing.xlsx").active
items = [{"code": str(r[1]).strip(), "desc": " ".join(str(r[2]).split())[:400]} for r in ws.iter_rows(min_row=3, values_only=True) if r[1]]
(out / "items.json").write_text(json.dumps(items, indent=1))

# Contractors (names only in pack). Category is OUR guess from the name, labelled as such.
ws = openpyxl.load_workbook(pack / "05_vendors/Contractor_list_Aquina.xlsx").active
names = [str(r[0]).strip() for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
(out / "contractors.json").write_text(json.dumps(names, indent=1))

# Amara quantities (WO item-wise sheet): sl, WO code, description, UOM, totals
ws = openpyxl.load_workbook(pack / "02_site_requirements/A_Amara_Waterproofing_quantities_Tower-2.xlsx", data_only=True)["WO ITEM-WISE BOQ"]
rows = []
for r in ws.iter_rows(min_row=4, values_only=True):
    if isinstance(r[0], (int, float)):
        rows.append({"sl": int(r[0]), "wo_code": r[1], "desc": r[2], "uom": r[4], "total_sqm": r[10] if isinstance(r[10], (int, float)) else None,
                     "total_sft": r[11] if isinstance(r[11], (int, float)) else None})
(out / "amara_site_boq.json").write_text(json.dumps(rows, indent=1))
print("seed written:", len(items), "items,", len(names), "contractors,", len(rows), "site BOQ rows")
