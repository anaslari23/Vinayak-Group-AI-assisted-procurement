"""Step 2: match a requirement to an ERP item code, flag duplicates, or suggest a new code.
Order: exact attributes -> fuzzy (rapidfuzz) -> LLM only for borderline. Item codes/columns are ASSUMED Farvision-style."""
import re
from rapidfuzz import fuzz
from .db import con

SURFACES = {"toilet": ("toilet", "w.c", "wc", "bathroom"), "kitchen": ("kitchen", "wash", "utility"), "balcony": ("balcony", "sky terrace", "terrace"),
            "roof": ("roof", "mumpty"), "ugr": ("ugr", "underground"), "stp": ("stp",), "lift pit": ("lift pit",), "ot": ("overhead", "owht", "ot floor")}
ELEMENTS = {"floor": ("floor",), "wall": ("wall",), "depression": ("depression",)}


def attrs(text: str) -> dict:
    t = text.lower()
    s = next((k for k, v in SURFACES.items() if any(w in t for w in v)), None)
    e = next((k for k, v in ELEMENTS.items() if any(w in t for w in v)), None)
    h = re.search(r"(\d+)\s*mm", t)
    return {"surface": s, "element": e, "height_mm": int(h.group(1)) if h else None}


def _norm(t):  # compare on the heading part only (long method text drowns the signal)
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()[:90]).strip()


def load_items():
    c = con(); r = [dict(x) for x in c.execute("select code, descr from items")]; c.close(); return r


def duplicate_groups(items: list[dict], thr=90):
    """Group codes whose descriptions are near-identical; these are the duplicates the HOD should consolidate."""
    seen, groups = set(), []
    for i, a in enumerate(items):
        if i in seen: continue
        g = [a]
        for j in range(i + 1, len(items)):
            b = items[j]
            if j not in seen and fuzz.ratio(_norm(a["descr"]), _norm(b["descr"])) >= thr:
                g.append(b); seen.add(j)
        if len(g) > 1: groups.append(g)
    return groups


def match(requirement: str, category_hint: str | None = None) -> dict:
    items = load_items()
    ra = attrs(requirement)
    scored = []
    for it in items:
        ia = attrs(it["descr"])
        fz = fuzz.token_set_ratio(_norm(requirement), _norm(it["descr"]))
        attr_hit = ra["surface"] and ra["surface"] == ia["surface"] and ra["element"] == ia["element"]
        s = fz * 0.6 + (40 if attr_hit else 0) + (0 if re.search(r"\.[AB]$", it["code"]) else 3)  # prefer the plain code over -A/-B variants
        scored.append({**it, "score": round(s, 1), "fuzzy": round(fz, 1), "attr_match": bool(attr_hit)})
    scored.sort(key=lambda x: -x["score"])
    count = {}
    for x in scored: count[x["code"]] = count.get(x["code"], 0) + 1
    uniq, seen_c = [], set()
    for x in scored:
        if x["code"] in seen_c: continue
        seen_c.add(x["code"]); x["code_repeats"] = count[x["code"]]; uniq.append(x)
    scored = uniq
    top = scored[:5]
    best = top[0] if top else None
    # near-duplicates of the best hit (same text, different code)
    dups = [x for x in scored if best and x["code"] != best["code"] and fuzz.ratio(_norm(x["descr"]), _norm(best["descr"])) >= 90]
    if best and best["attr_match"] and best["score"] >= 85:
        verdict = "match"
    elif best and best["score"] >= 60:
        verdict = "borderline"
    else:
        verdict = "new"
    out = {"verdict": verdict, "attrs": ra, "candidates": top, "duplicates_of_best": dups[:5]}
    if verdict == "new":
        out["suggested_code"] = suggest_code(requirement, items)
    return out


def suggest_code(requirement: str, items: list[dict]) -> dict:
    """ASSUMED convention: <CATEGORY>.<NN> following the existing WP.NN pattern. Real Farvision rules are not public."""
    t = requirement.lower()
    cat = "RD" if any(w in t for w in ("road", "paver", "kerb", "sand", "brick", "stone dust")) else "GN"
    used = [int(m.group(1)) for it in items if (m := re.match(rf"{cat}\.(\d+)$", it["code"]))]
    return {"code": f"{cat}.{(max(used) + 1) if used else 1:02d}", "note": "ASSUMED numbering, to be confirmed by the back-office HOD"}
