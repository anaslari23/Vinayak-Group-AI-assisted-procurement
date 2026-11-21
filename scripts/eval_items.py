"""Score the item-code matcher on data/item_tests.json against the dataset in DATA_DIR. Writes <DATA_DIR>/item_eval.json."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db, itemmatch as im
from app.tender import DATA_DIR

def run():
    db.init(reset=True); im.erp(reload=True)
    meta = json.loads((DATA_DIR / "item_meta.json").read_text())["aliases"]
    tests = json.loads((Path(__file__).resolve().parent.parent / "data" / "item_tests.json").read_text())
    rows, secs = [], []
    for t in tests:
        r = im.match(t["text"]); secs.append(r["seconds"])
        exp = {c for a in t["expect_top"] for c in meta.get(a, [a])}
        top = [c["code"] for c in r["candidates"]]
        got_flags = {f["flag"] for f in r["flags"]}
        rows.append({**t, "status": r["status"], "top": top, "flags": sorted(got_flags),
                     "status_ok": r["status"] == t["expect_status"],
                     "top1": (top[:1] and top[0] in exp) if exp else None, "top3": bool(set(top) & exp) if exp else None,
                     "flags_ok": set(t["expect_flags"]) <= got_flags if t["expect_flags"] else None,
                     "false_conf": r["status"] == "matched_high" and (t["expect_status"] != "matched_high" or (exp and top[:1] and top[0] not in exp))})
    def pct(xs):
        xs = [x for x in xs if x is not None]; return (sum(bool(x) for x in xs), len(xs))
    def block(sub):
        return {"cases": len(sub), "status_correct": pct(r["status_ok"] for r in sub), "top1": pct(r["top1"] for r in sub), "top3": pct(r["top3"] for r in sub),
                "flags_found": pct(r["flags_ok"] for r in sub), "false_confidence": sum(1 for r in sub if r["false_conf"]),
                "right_question": pct(r["status_ok"] for r in sub if r["expect_status"] in ("needs_more_info", "no_match"))}
    out = {"dataset": str(DATA_DIR.name), "erp_rows": len(im.erp().rows), "overall": block(rows), "spec14": block([r for r in rows if r["id"].startswith("S")]),
           "own_visible": block([r for r in rows if r["id"].startswith("O") and not r["hidden"]]), "own_hidden": block([r for r in rows if r["hidden"]]),
           "mean_seconds": round(sum(secs) / len(secs), 3), "failures": [{k: r[k] for k in ("id", "text", "expect_status", "status", "top", "flags")} for r in rows if not r["status_ok"]]}
    (DATA_DIR / "item_eval.json").write_text(json.dumps(out, indent=1)); return out

if __name__ == "__main__":
    o = run()
    for k in ("overall", "spec14", "own_visible", "own_hidden"): print(k, o[k])
    print("mean s", o["mean_seconds"], "| rows", o["erp_rows"])
    for f in o["failures"]: print("FAIL", f)
