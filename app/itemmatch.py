"""Step 2: item-code matching agent, per the Step 2 build spec.
Deterministic and explainable (no model in the scoring). Input: one request line. Output: ranked candidates with reasons, duplicate and
conflict flags, a status, and a question or a new-code draft when needed. It never creates or edits a code; it only recommends."""
import json, re, time
from collections import Counter, defaultdict
from datetime import datetime
from rapidfuzz import fuzz, process
from . import db
from .tender import DATA_DIR

TYPOS = {"hight": "height", "expension": "expansion", "waterproofinworks": "waterproofing works", "waterproofinwork": "waterproofing work", "waterprofing": "waterproofing",
         "waterproofng": "waterproofing", "waterprooing": "waterproofing", "kitchn": "kitchen", "puning": "punning", "grouing": "grouting", "polysulfide": "polysulphide"}
STOP = {"at", "of", "the", "and", "for", "in", "on", "a", "an", "to", "upto", "up", "work", "works", "if", "required", "ka", "ki", "ke", "hai", "ho", "me", "mein", "chahiye", "lagana",
        "need", "needed", "please", "new", "sqft", "sqm", "rmt", "nos", "with", "by", "is", "flat", "type", "side", "mm", "extra", "charges", "i", "we", "kya", "karna", "wala"}
AREAS = {"toilet": {"toilet"}, "kitchen": {"kitchen", "wash", "utility"}, "balcony": {"balcony"}, "roof": {"roof", "mumpty"}, "ugr": {"ugr"}, "stp": {"stp"},
         "lift pit": {"lift", "pit"}, "ot": {"ot", "owht", "overhead"}}
SURFACES = {"floor": {"floor"}, "wall": {"wall"}, "depression": {"depression", "channel"}}
WORKS = {"waterproofing": {"waterproofing", "waterproof", "proofing"}, "grouting": {"grouting", "grout", "nozzle"}, "screed": {"screed"}, "punning": {"punning"},
         "expansion joint": {"expansion", "joint"}, "pumping": {"pump", "pumping"}, "repair": {"repairing", "repair"}, "coba": {"coba"}}
OTHER = {"road": {"road", "paver", "kerb", "saucer", "wbm", "jhama"}, "steel": {"steel", "tmt", "rebar", "bars", "bar"}, "cement": {"cement", "opc", "ppc"},
         "civil material": {"sand", "brick", "bricks", "aggregate", "stone", "dust"}, "finishing": {"tile", "tiles", "paint", "putty"}}
PRODUCT_WORDS = {"polysulphide", "hypalon", "epoxy", "polyurethane", "acrylic", "pu", "bitumen"}
AREA_UNITS = {"sqft", "sqm", "sft", "sq"}
LOADED = {"waterproofing"}


# ---------------------------------------------------------------- cleaning
def clean(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[*{}\[\]()\"'_]", " ", t); t = t.replace("&", " and "); t = re.sub(r"\.(?=\w)", "", t)  # w.c -> wc
    t = re.sub(r"[-/.,:;]+", " ", t)
    toks = [TYPOS.get(w, w) for w in t.split()]
    return " ".join(" ".join(toks).split())


def title_of(desc: str) -> str:
    """Matching uses the first title phrase; the long specification stays as detail."""
    d = re.split(r"\s(?:providing|supply\s*&|supply and)\s", desc, maxsplit=1, flags=re.I)[0]
    d = re.split(r":", d, maxsplit=1)[0]
    return d[:140]


def _stem(t: str) -> str:
    return re.sub(r"(ing|es|s)$", "", t) if len(t) > 5 else t


def family_key(c: str) -> str:
    """Same work, ignoring filler words, plurals/-ing and a trailing side letter A/B."""
    toks = c.split()
    if toks and toks[-1] in ("a", "b"): toks = toks[:-1]
    return " ".join(sorted(_stem(t) for t in _content(toks)))


def code_key(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.lower())


def code_base(code: str) -> str:
    k = code_key(code)
    return re.sub(r"[ab]$", "", k) if re.search(r"\d[ab]$", k) else k


def _synonyms():
    f = DATA_DIR / "synonyms.json"
    base = json.loads(f.read_text()) if f.exists() else {}
    try:
        c = db.con(); extra = {r["word"]: r["maps_to"] for r in c.execute("select word, maps_to from synonyms")}; c.close(); base.update(extra)
    except Exception:
        pass
    return base


# ---------------------------------------------------------------- the ERP list: load once, group, flag
class ErpList:
    def __init__(self):
        c = db.con(); rows = [dict(r) for r in c.execute("select rowid as row, code, descr from items order by rowid")]; c.close()
        for r in rows:
            r["title"] = clean(title_of(r["descr"])); r["fam"] = family_key(r["title"])
        self.rows = rows
        try:
            c = db.con(); m = c.execute("select value from master_meta where key='erp_loaded'").fetchone(); c.close()
        except Exception:
            m = None
        f = DATA_DIR / "items.json"
        self.loaded = m["value"] if m else (datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if f.exists() else "unknown")
        self.vocab = {w for r in rows for w in r["title"].split()} | set(_synonyms())
        by_code = defaultdict(list)
        for r in rows: by_code[r["code"]].append(r)
        self.by_code = by_code
        self.exact_repeat = {c: rs for c, rs in by_code.items() if len(rs) > 1 and len({x["title"] for x in rs}) == 1}
        self.conflicting = {c: rs for c, rs in by_code.items() if len({x["title"] for x in rs}) > 1}
        fam = defaultdict(set)
        for r in rows: fam[r["fam"]].add(r["code"])
        self.family_codes = fam
        look = defaultdict(set)
        for r in rows: look[code_base(r["code"])].add(r["code"])
        self.lookalike = {k: v for k, v in look.items() if len(v) > 1}
        self.taken = {r["code"] for r in rows}

    def flags_for(self, codes):
        out, seen = [], set()
        def add(flag, msg):
            if (flag, msg) not in seen: seen.add((flag, msg)); out.append({"flag": flag, "message": msg})
        for c in codes:
            if c in self.exact_repeat:
                rows = ", ".join(str(r["row"]) for r in self.exact_repeat[c]); add("erp_exact_repeat", f"Code {c} appears {len(self.exact_repeat[c])} times (rows {rows}). Same meaning. Safe to treat as one.")
            if c in self.conflicting:
                parts = "; ".join(f"row {r['row']}: {r['title']}" for r in self.conflicting[c])
                add("erp_conflicting_code", f"Code {c} means different things ({parts}). Do not use until fixed.")
        fams = {family_key(clean(title_of(r["descr"]))) for c in codes for r in self.by_code.get(c, [])}
        for f in fams:
            codes_f = sorted(self.family_codes.get(f, []))
            if len(codes_f) > 1:
                add("erp_same_work_many_codes", f"Same work has {len(codes_f)} codes: {', '.join(codes_f)}. Choose one standard code.")
                if len({code_base(x) for x in codes_f}) == 1:
                    add("suffix_variant", f"{', '.join(codes_f)} share the same text. Is the A or B version needed, or the plain one? Ask which flat type.")
        for c in codes:
            sib = self.lookalike.get(code_base(c), set()) - {c}
            for o in sorted(sib):
                if {x["title"] for x in self.by_code[o]} != {x["title"] for x in self.by_code[c]} and family_key(self.by_code[o][0]["title"]) != family_key(self.by_code[c][0]["title"]):
                    add("erp_lookalike_code", f"Look-alike codes {c} and {o} are different work. Check before ordering.")
        return out


_ERP = None


def erp(reload=False):
    global _ERP
    if _ERP is None or reload: _ERP = ErpList()
    return _ERP


def cleanup_report(hits: dict | None = None):
    e = erp(); hits = hits or {}
    out = []
    for c, rs in e.conflicting.items(): out.append({"flag": "erp_conflicting_code", "codes": [c], "detail": "; ".join(f"row {r['row']}: {r['title']}" for r in rs)})
    for c, rs in e.exact_repeat.items(): out.append({"flag": "erp_exact_repeat", "codes": [c], "detail": f"{len(rs)} rows ({', '.join(str(r['row']) for r in rs)}): {rs[0]['title']}"})
    for f, cs in e.family_codes.items():
        if len(cs) > 1: out.append({"flag": "erp_same_work_many_codes", "codes": sorted(cs), "detail": f})
    for k, cs in e.lookalike.items(): out.append({"flag": "erp_lookalike_code", "codes": sorted(cs), "detail": "codes differ only by dot, dash, underscore or suffix"})
    for o in out: o["hits"] = sum(hits.get(c, 0) for c in o["codes"])
    return sorted(out, key=lambda o: (-o["hits"], o["flag"]))


# ---------------------------------------------------------------- reading the request
def normalise_request(text: str):
    syn = _synonyms(); e = erp()
    toks = []
    for w in clean(text).split():
        w = syn.get(w, w)
        if w not in e.vocab and w not in STOP and len(w) >= 5 and not w.isdigit():
            m = process.extractOne(w, e.vocab, scorer=fuzz.ratio, score_cutoff=82)
            if m: w = m[0]
        toks.extend(w.split())
    return toks


def parts_of(toks):
    s = set(toks)
    area = next((a for a, ws in AREAS.items() if s & ws), None)
    surface = next((a for a, ws in SURFACES.items() if s & ws), None)
    work = next((a for a, ws in WORKS.items() if s & ws), None)
    return {"area": area, "surface": surface, "work": work, "side": None, "tokens": s}


def side_of(raw: str):
    m = re.search(r"\b(?:flat|type|side|version)\s*[-]?\s*([ab])\b|[-\s]([ab])\s*$", raw.lower().strip())
    return (m.group(1) or m.group(2)) if m else None


def _content(toks):
    return [t for t in toks if (t not in STOP and t not in AREA_UNITS and not re.fullmatch(r"\d+(\.\d+)?", t)) or t in ("450", "200")]


def height_cue(raw: str):
    m = re.search(r"(\d{3})\s*mm", raw.lower())
    if not m: return None
    return {"450": ("toilet", "wall"), "200": ("balcony", "wall")}.get(m.group(1))


# ---------------------------------------------------------------- scoring
def level(x): return "high" if x >= 0.85 else ("medium" if x >= 0.60 else "low")


def score_row(req_tokens, rq_parts, row_title, unit, side):
    rt = {_stem(t) for t in _content(row_title.split())}; qt = {_stem(t) for t in _content(req_tokens)}
    if not qt: return 0.0, []
    inter = qt & rt
    cover = len(inter) / len(qt); jac = len(inter) / max(1, len(qt | rt))
    overlap = 0.75 * cover + 0.25 * jac
    fz = fuzz.token_set_ratio(" ".join(sorted(qt)), " ".join(sorted(rt))) / 100
    rp = parts_of(row_title.split()); agree, n, why = 0.0, 0, []
    rt_raw = set(row_title.split())
    for key in ("area", "surface"):
        if rq_parts[key]:
            n += 1
            if rp[key] == rq_parts[key]: agree += 1; why.append(f"{key} {rq_parts[key]} agrees")
            elif rp[key] is None and key == "surface" and "floor" in rt_raw and "wall" in rt_raw: agree += 0.7; why.append("covers floor and wall")
            else: why.append(f"{key} differs ({rq_parts[key]} vs {rp[key] or 'none'})")
    if rq_parts["work"]:
        n += 1
        if rp["work"] == rq_parts["work"]: agree += 1
    parts = agree / n if n else 0.5
    sc = 0.5 * overlap + 0.33 * fz + 0.17 * parts
    for key, pen in (("surface", 0.15), ("area", 0.20)):
        if rq_parts[key] and rp[key] and rp[key] != rq_parts[key]: sc -= pen
    if unit and unit.lower() in ("rmt", "rm", "nos", "no") and "depression" not in rt_raw and "pump" not in rt and "fixing" not in rt_raw and rp["area"]:
        sc -= 0.10; why.append(f"unit {unit.upper()} does not fit an area-based item")
    if side and row_title.strip().endswith(" " + side): sc += 0.02
    return max(0.0, min(0.99, sc)), why


# ---------------------------------------------------------------- the pipeline
def match(text: str, unit: str | None = None, site_code_hint: str | None = None, quantity=None, project: str | None = None, request_id=None) -> dict:
    t0 = time.perf_counter(); e = erp()
    out = {"request_id": request_id, "raw_text": text, "status": None, "candidates": [], "duplicate_warning": None, "new_code_suggestion": None,
           "questions_for_requester": [], "flags": [], "message": None, "erp_loaded": e.loaded, "learned": False}
    toks = normalise_request(text); cont = _content(toks)
    rq = parts_of(toks); hc = height_cue(text)
    if hc and not rq["area"]: rq["area"], rq["surface"] = hc
    elif hc and not rq["surface"]: rq["surface"] = hc[1]
    side = side_of(text)
    out["parts"] = {k: v for k, v in rq.items() if k != "tokens"} | {"side": side}
    s = set(toks)
    other = next((cat for cat, ws in OTHER.items() if s & ws), None)
    domain = bool(rq["area"] or rq["work"] or rq["surface"])
    if not cont or (not domain and not other and max([fuzz.token_set_ratio(" ".join(cont), r["title"]) for r in e.rows] or [0]) < 45):
        out.update(status="no_match", message="I could not find an item in that. Please tell me what is needed (for example: toilet floor waterproofing, 13,400 sqft).")
        return _done(out, t0)
    if other and not domain:
        ask = ["Which grade or type, size or diameter, and quantity?"] if other in ("steel", "road", "cement") else ["What exactly is needed, and the quantity?"]
        out.update(status="needs_more_info", message=f"No code list loaded for this category ({other}). I will not invent a code; the back office must supply the list or create the code.", questions_for_requester=ask)
        return _done(out, t0)
    # learned choices (the back office's earlier picks for the same wording)
    learned = _learned(" ".join(cont))
    # the site sheet may already name this item with a code; compare that code's ERP meaning to what the site wrote
    explicit_hint = site_code_hint          # only a code the caller passed counts as an exact hint
    if not site_code_hint:
        sc = _site_code(" ".join(cont))
        if sc: site_code_hint = sc          # looked up from the site sheet, used only to spot code/text mismatches
    if site_code_hint:
        rows = e.by_code.get(site_code_hint)
        if rows:
            ht = rows[0]["title"]
            if family_key(ht) != family_key(clean(text)) and fuzz.token_set_ratio(" ".join(cont), " ".join(_content(ht.split()))) < 70:
                out["flags"].append({"flag": "code_text_mismatch", "message": f"Site says {site_code_hint} = {clean(text)}. ERP says {site_code_hint} = {ht}. Trusting the text."})
    # score every ERP family on its best row
    fam_best = {}
    for r in e.rows:
        sc, why = score_row(cont, rq, r["title"], unit, side)
        f = r["fam"]
        if f not in fam_best or sc > fam_best[f][0]: fam_best[f] = (sc, why, r)
    ranked = sorted(fam_best.values(), key=lambda x: -x[0])
    if not ranked: out.update(status="no_match", message="Nothing to match against."); return _done(out, t0)
    top_sc, top_why, top_row = ranked[0]
    # required parts for waterproofing items (area and surface), per spec
    work = rq["work"]
    if work == "waterproofing" and not (rq["area"] in ("ugr", "stp", "lift pit", "ot", "roof")):
        miss = [k for k in ("area", "surface") if not rq[k]]
        if miss:
            q = "Which area (toilet, kitchen, balcony, roof) and is it floor or wall?" if len(miss) == 2 else ("Floor or wall?" if miss == ["surface"] else "Which area (toilet, kitchen, balcony, roof)?")
            out.update(status="needs_more_info", questions_for_requester=[q], message="I need one more detail before matching.")
            out["candidates"] = _cands(ranked[:3], e, learned, side); return _done(out, t0)
    # candidates: expand the best family to its codes (plain first), fill to 3 with other families
    cands = _cands(ranked[:3], e, learned, side)
    out["candidates"] = cands
    top_codes = [c["code"] for c in cands]
    top_code = cands[0]["code"]
    out["flags"] += e.flags_for(top_codes)
    if site_code_hint and site_code_hint in e.by_code and any(f["flag"] == "code_text_mismatch" for f in out["flags"]) is False: pass
    fl = {f["flag"] for f in out["flags"]}
    margin_close = len(ranked) > 1 and top_sc - ranked[1][0] < 0.05 and ranked[1][2]["fam"] != top_row["fam"]
    prod = {w for r in e.by_code.get(top_code, []) for w in r["title"].split()} & PRODUCT_WORDS
    # product ambiguity: one code, two different products -> ask which
    if top_code in e.conflicting and len({tuple(sorted(set(r["title"].split()) & PRODUCT_WORDS)) for r in e.conflicting[top_code]}) > 1 and not (set(cont) & PRODUCT_WORDS):
        names = sorted({w for r in e.conflicting[top_code] for w in r["title"].split() if w in PRODUCT_WORDS})
        out.update(status="needs_more_info", questions_for_requester=[f"Which material: {' or '.join(n.capitalize() for n in names)}?"], message=f"{top_code} has two meanings in the ERP.")
        return _done(out, t0)
    mism = any(f["flag"] == "code_text_mismatch" for f in out["flags"])
    in_conflict = top_code in e.conflicting
    # a code the site sheet already named, whose ERP text agrees with the request, is an exact hint
    exact_hint = bool(explicit_hint and explicit_hint == top_code and not mism and not in_conflict)
    if in_conflict or mism:
        out["status"] = "conflict_in_erp"; out["message"] = "The ERP itself is unclear for this item. Back office to decide; I will not pick."
    elif top_sc < 0.60 and not exact_hint:
        out["status"] = "suggest_new_code"; out["new_code_suggestion"] = _suggest(text, rq, unit, ranked[:3], e)
        out["message"] = "Nothing matches well. A draft new code is below for the back office."
    elif exact_hint or (level(top_sc) == "high" and not (fl - {"erp_exact_repeat"}) and not margin_close):
        out["status"] = "matched_high"
    else:
        out["status"] = "needs_back_office_confirm"
    if learned and out["status"] in ("needs_back_office_confirm", "matched_high") and top_code not in e.conflicting:
        out["learned"] = True
        if out["status"] == "needs_back_office_confirm" and learned["code"] == cands[0]["code"] and not (fl - {"erp_exact_repeat", "suffix_variant", "erp_same_work_many_codes"}):
            out["status"] = "matched_high"
    dups = [c["code"] for c in cands if family_key(clean(title_of(e.by_code[c["code"]][0]["descr"]))) == top_row["fam"]]
    if len(dups) > 1:
        out["duplicate_warning"] = {"group": dups, "note": f"{len(dups)} codes for the same work. Back office to choose one."}
    if quantity is None and out["status"] in ("matched_high", "needs_back_office_confirm"):
        out["flags"].append({"flag": "quantity_missing", "message": "No quantity given; passing this back to Step 1."})
    return _done(out, t0)


def _cands(ranked, e, learned, side=None):
    out, seen = [], set()
    top_sc, top_why, top_row = ranked[0]
    def order(c):
        has = re.search(r"\.([AB])$", c)
        return (0 if (side and has and has.group(1).lower() == side) else (1 if not has else 2), c)
    base = code_base(top_row["code"])
    fam_codes = sorted(e.family_codes[top_row["fam"]], key=lambda c: (code_base(c) != base,) + order(c))
    for c in fam_codes:
        out.append({"code": c, "description": e.by_code[c][0]["descr"][:120], "confidence": round(top_sc - (0.02 if re.search(r"\.[AB]$", c) else 0), 2),
                    "why": ", ".join(top_why) or "same words" + (", suffix " + c[-1] if re.search(r"\.[AB]$", c) else ""), "rows": [r["row"] for r in e.by_code[c]]}); seen.add(c)
    for sc, why, r in ranked[1:]:
        if len(out) >= 3: break
        if r["code"] in seen: continue
        out.append({"code": r["code"], "description": r["descr"][:120], "confidence": round(sc, 2), "why": ", ".join(why) or "weaker word match", "rows": [x["row"] for x in e.by_code[r["code"]]]}); seen.add(r["code"])
    out = out[:3]
    if learned and learned["code"] in e.by_code:
        out = [c for c in out if c["code"] != learned["code"]]
        out.insert(0, {"code": learned["code"], "description": e.by_code[learned["code"]][0]["descr"][:120], "confidence": 0.97, "why": "the back office picked this for the same wording before", "rows": [r["row"] for r in e.by_code[learned["code"]]]})
        out = out[:3]
    for c in out: c["level"] = level(c["confidence"])
    return out


def _suggest(text, rq, unit, nearest, e):
    pat = re.compile(r"^([A-Z]+)\.(\d{2})(?:\.[AB])?$")
    pref = Counter(m.group(1) for c in e.taken if (m := pat.match(c)))
    prefix = pref.most_common(1)[0][0] if pref else "WP"
    used = sorted({int(m.group(2)) for c in e.taken if (m := pat.match(c)) and m.group(1) == prefix})
    nxt = next(n for n in range(1, 100) if n not in used)
    desc = " ".join(x for x in ["WATERPROOFING" if (rq["work"] in (None, "waterproofing")) else (rq["work"] or "").upper(), "AT" if rq["area"] else "", (rq["area"] or "").upper(), (rq["surface"] or "").upper()] if x).strip()
    rej = []
    for sc, why, r in nearest:
        rej.append({"code": r["code"], "why_rejected": ("; ".join(w for w in why if "differs" in w) or "meaning too different") + f" (score {sc:.2f})"})
    return {"code": f"{prefix}.{nxt:02d}", "description": desc or text.upper()[:60], "unit": (unit or "SQM").upper(), "work_or_material": "work",
            "taken_numbers": used, "nearest": rej, "needs": "back_office_approval"}


def _site_code(clean_req: str):
    f = DATA_DIR / "site_boq.json"
    if not f.exists(): return None
    best = None
    for r in json.loads(f.read_text()):
        sc = fuzz.token_set_ratio(clean_req, " ".join(_content(clean(r["desc"]).split())))
        if sc >= 90 and (not best or sc > best[0]): best = (sc, r["wo_code"])
    return best[1] if best else None


def _learned(clean_req: str):
    try:
        c = db.con(); r = c.execute("select code from item_choices where clean_req=? order by id desc limit 1", (clean_req,)).fetchone(); c.close()
        return {"code": r["code"]} if r else None
    except Exception:
        return None


def remember(text: str, code: str, project=None, who="back office"):
    c = db.con(); c.execute("insert into item_choices(clean_req, code, project, who) values(?,?,?,?)", (" ".join(_content(normalise_request(text))), code, project, who)); c.commit(); c.close()


def _done(out, t0):
    out["seconds"] = round(time.perf_counter() - t0, 3)
    return out


def split_lines(text: str):
    """Several items in one line: split first, match each on its own."""
    parts = [p.strip() for p in re.split(r"\n|;|\s\+\s|,\s+and\s+|\s+and\s+(?=\w+\s+(?:floor|wall|waterproofing|grouting|screed))|,\s*(?=\w+\s+(?:floor|wall|waterproofing|grouting|screed))", text) if p.strip()]
    return parts or [text]


def match_many(text: str, **kw):
    return [match(p, **kw) for p in split_lines(text)]


def log_run(request_id, asked_by, project, result, approved_by=None, code=None):
    """Append-only audit line: who asked, which ERP list, what was found, who approved which code."""
    c = db.con()
    c.execute("insert into item_log(request_id,asked_by,project,erp_loaded,raw_text,status,candidates,flags,approved_by,code) values(?,?,?,?,?,?,?,?,?,?)",
              (str(request_id), asked_by, project, result.get("erp_loaded"), result.get("raw_text"), result.get("status"),
               json.dumps([(x["code"], x["confidence"]) for x in result.get("candidates", [])]), json.dumps([f["flag"] for f in result.get("flags", [])]), approved_by, code))
    c.commit(); c.close()


def flag_hits():
    """How often each code was touched by a match run: tells the ERP owner what to fix first."""
    c = db.con(); rows = c.execute("select candidates from item_log").fetchall(); c.close()
    hits = Counter()
    for r in rows:
        for code, _ in json.loads(r["candidates"] or "[]"): hits[code] += 1
    return hits
