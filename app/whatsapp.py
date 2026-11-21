"""WhatsApp group front door (via the Baileys bridge in /whatsapp). The agent TRACKS every group message it can see and acts on its own:
it notices a requirement without being asked, takes it forward, asks the engineer what is missing, and brings the group's admins (the HOD role)
the decision. Nobody has to type a command. WARNING: the bridge uses an unofficial WhatsApp Web login: against WhatsApp's terms, use a spare number."""
import base64, json, os, re, threading
from . import db, engine, stock, voice
from .llm import ask_json

STATUS = {"state": "bridge not connected", "qr": None, "me": None}
ADMINS: dict = {}
TRIGGER = re.compile(r"^\s*(@?agent|@?bot|@?procure\w*|@?vinayak)\b[\s,:\-]*", re.I)
NEED = re.compile(r"\b(need|needed|require|required|chahiye|chahie|lagbe|laagbe|zaroorat|want|send|bhejo|arrange|urgent|order|stock out|khatam|finished)\b|चाहिए|ज़रूरत|জরুরি|লাগবে", re.I)
QTY = re.compile(r"\b\d[\d,\.]*\s*(sqft|sq ?ft|sqm|sq ?m|rmt|nos|no\.|bags?|kg|ton|tons|tonnes|cum|cft|ltr|litres?|pack|packs|rolls?|pcs|pieces)\b", re.I)
DOMAIN = {"waterproofing", "grouting", "screed", "punning", "paver", "kerb", "tmt", "cement", "sand", "steel", "brick", "bricks", "tile", "tiles", "paint", "scaffolding", "pump", "aggregate", "stone", "dust", "expansion", "joint", "coba", "putty"}
SCRIPT = [("hi-IN", "ऀ", "ॿ"), ("bn-IN", "ঀ", "৿"), ("ta-IN", "஀", "௿"), ("te-IN", "ఀ", "౿"), ("kn-IN", "ಀ", "೿"), ("ml-IN", "ഀ", "ൿ"), ("gu-IN", "઀", "૿"), ("pa-IN", "਀", "੿")]


def lang_of(text):
    for code, a, b in SCRIPT:
        if any(a <= ch <= b for ch in text): return code
    return "en-IN"


def num(jid): return jid.split("@")[0].split(":")[0]


def requirement_like(text: str):
    """Cheap prefilter, then a model check. Returns a short summary or None."""
    t = text.lower(); words = set(re.findall(r"[a-z]+", t))
    mats = {w for m in stock.materials() for w in re.findall(r"[a-z]+", (m["name"] + " " + (m["synonyms"] or "")).lower())}
    if not (QTY.search(t) or (NEED.search(t) and (words & (DOMAIN | mats)))) or len(text) < 12: return None
    try:
        r = ask_json('This is a message in a site-engineers WhatsApp group (any Indian language, maybe Hinglish). Is it someone asking for material or work to be procured (a requirement), '
                     'as opposed to chat, a status update or a question? Return JSON {"is_requirement": true|false, "summary": "one short English phrase"}.\nMESSAGE: ' + text.replace('"', "'"), max_tokens=120)
        return r.get("summary") or text[:60] if r.get("is_requirement") else None
    except Exception:
        return None


def _hod_buttons(case_id):
    c = db.con(); r = c.execute("select buttons from chat_messages where case_id=? and buttons is not null order by id desc limit 1", (case_id,)).fetchone(); c.close()
    return json.loads(r["buttons"]) if r else []


def pick_option(text: str, buttons: list):
    """Which option did the admin choose? A number, a clear word, or (failing that) the model."""
    t = text.strip().lower().strip(".!")
    if re.fullmatch(r"\d", t) and 1 <= int(t) <= len(buttons): return buttons[int(t) - 1]
    words = {"approve": "decision:approve", "approved": "decision:approve", "yes": "decision:approve", "ok": "decision:approve", "haan": "decision:approve", "reject": "decision:reject", "no": "decision:reject", "nahi": "decision:reject", "send back": "decision:send back"}
    for w, data in words.items():
        if t == w or t.startswith(w + " "):
            b = next((b for b in buttons if b["data"] == data), None)
            if b: return b
    try:
        r = ask_json("Options:\n" + "\n".join(f"{i}. {b['label']}" for i, b in enumerate(buttons, 1)) + f'\nA decision-maker replied: "{text}". Which option do they mean? Return JSON {{"choice": <number or 0 if unclear>}}.', max_tokens=40)
        n = int(r.get("choice", 0)); return buttons[n - 1] if 1 <= n <= len(buttons) else None
    except Exception:
        return None


def _queue(jid, text, mentions=None, ogg=None):
    c = db.con(); c.execute("insert into wa_out(jid,text,mentions,ogg_b64) values(?,?,?,?)", (jid, text, json.dumps(mentions or []), ogg)); c.commit(); c.close()


def _cases_for(jid, sender=None):
    c = db.con()
    q = "select w.*, c.state, c.waiting from wa_cases w join cases c on c.id=w.case_id where w.jid=?" + (" and w.sender=?" if sender else "") + " order by w.case_id desc"
    r = [dict(x) for x in c.execute(q, (jid, sender) if sender else (jid,))]; c.close(); return r


def inbound(p: dict) -> dict:
    """One message seen in a chat. Always tracked; acted on when the agent can help."""
    c = db.con()
    if c.execute("select 1 from wa_messages where msg_id=?", (p["msg_id"],)).fetchone(): c.close(); return {"ok": True, "dup": True}
    text, kind, lang = (p.get("text") or "").strip(), "text", None
    if p.get("audio_b64"):
        try: text, lang = voice.stt(base64.b64decode(p["audio_b64"]), "voice.ogg"); kind = "voice"
        except Exception as e: text = ""
    jid, sender = p["jid"], p["sender"]; name = p.get("sender_name") or num(sender)
    if p.get("admins"): ADMINS[jid] = p["admins"]
    cur = c.execute("insert into wa_messages(msg_id,jid,group_name,sender,sender_name,text,kind) values(?,?,?,?,?,?,?)", (p["msg_id"], jid, p.get("group_name"), sender, name, text, kind)); c.commit(); mid = cur.lastrowid; c.close()
    if not text: return {"ok": True, "handled": "empty"}
    mine = set(p.get("my_ids") or []); me_nums = {num(x) for x in mine}
    addressed = (not p.get("is_group")) or bool(TRIGGER.match(text)) or any(num(m) in me_nums for m in p.get("mentioned", [])) or (p.get("quoted_participant") and num(p["quoted_participant"]) in me_nums)
    body = TRIGGER.sub("", text, count=1).strip() or text
    handled = _route(p, jid, sender, name, body, addressed, kind, lang)
    c = db.con(); c.execute("update wa_messages set handled=? where id=?", (handled, mid)); c.commit(); c.close()
    return {"ok": True, "handled": handled}


def _route(p, jid, sender, name, body, addressed, kind, lang):
    admins = ADMINS.get(jid, []); is_admin = (not p.get("is_group")) or any(num(a) == num(sender) or num(a) == num(p.get("sender_alt", "x")) for a in admins) or os.getenv("WA_HOD_NUMBERS", "").find(num(sender)) >= 0
    mine = _cases_for(jid)
    # 1. a decision for the HOD role (group admins): the latest case in this group that is waiting on the HOD
    pend = next((x for x in mine if x["waiting"] == "hod"), None)
    if pend and is_admin and (addressed or len(body) < 40):
        b = _hod_buttons(pend["case_id"]); opt = pick_option(body, b) if b else None
        if opt:
            engine.bg(engine.on_hod_action, pend["case_id"], opt["data"]); _queue(jid, f"Recorded for case #{pend['case_id']}: {opt['label']}.", [sender]); return "hod decision"
    # 2. addressed commands to the agent: help / status / a requirement or a question
    low = body.lower().strip()
    if addressed and low in ("help", "?", "hi", "hello"):
        _queue(jid, "I track this group and handle procurement on my own. Just say what is needed (text or voice, any language), e.g. 'need 200 bags cement at Demo Greens by 5 Dec'. I ask what is missing, check stock and item codes, send RFQs, read the quotes and bring the admins one recommendation. Ask me 'how much cement is left at Demo Heights?' any time.", [sender]); return "help"
    if addressed and re.fullmatch(r"(status|summary|cases)", low):
        rows = _cases_for(jid); _queue(jid, "Cases from this group:\n" + ("\n".join(f"#{r['case_id']} ({r['sender_name']}): {engine.STATE_TEXT.get(r['state'], r['state'])}" for r in rows[:8]) or "none yet"), [sender]); return "status"
    # 3. the sender's own open conversation (agent asked them something), or a check-in
    own = next((x for x in _cases_for(jid, sender) if x["waiting"] == "engineer"), None)
    if own:
        if own["state"] == "CHECKIN":
            b = _hod_buttons(own["case_id"]); opt = pick_option(body, b)
            if opt: engine.bg(engine.on_hod_action, own["case_id"], opt["data"]); return "check-in answer"
        engine.bg(_continue, own["case_id"], body); return "reply to the agent"
    if addressed:
        return _raise(jid, sender, name, body, kind, lang, ack=False)
    # 4. not addressed: the agent decides for itself whether this is a requirement
    summ = requirement_like(body)
    if summ: return _raise(jid, sender, name, body, kind, lang, ack=summ)
    return "tracked"


def _continue(cid, text):
    engine.on_engineer_message(cid, text)


def _raise(jid, sender, name, body, kind, lang, ack):
    lang = lang or lang_of(body)
    ch = "whatsapp (group, voice)" if kind == "voice" else "whatsapp (group)"
    cid = engine.engineer_inbound(body, ch)
    c = db.con(); c.execute("insert or ignore into wa_cases(case_id,jid,sender,sender_name,lang,voice) values(?,?,?,?,?,?)", (cid, jid, sender, name, lang, 1 if kind == "voice" else 0)); c.commit(); c.close()
    if ack: engine.log(cid, "agent", "note", f"Noticed a requirement in the WhatsApp group on its own: {ack}")
    return "raised case" if ack is False else "noticed a requirement"


def flush_cases():
    """Turn the agent's case messages into WhatsApp messages in the right group, in the sender's language."""
    c = db.con()
    rows = [dict(r) for r in c.execute("select cm.*, w.jid, w.sender, w.lang, w.voice from chat_messages cm join wa_cases w on w.case_id=cm.case_id where cm.wa_sent=0 order by cm.id")]
    for m in rows:
        text, mentions = m["text"], []
        if m["to_role"] == "hod":
            mentions = ADMINS.get(m["jid"], [])
            text = " ".join("@" + num(a) for a in mentions) + (" " if mentions else "") + f"[case #{m['case_id']}] " + text
        else:
            mentions = [m["sender"]]; text = "@" + num(m["sender"]) + " " + text
        if m["buttons"]:
            text += "\n" + "\n".join(f"{i}. {b['label']}" for i, b in enumerate(json.loads(m["buttons"]), 1)) + "\nReply with the number."
        ogg = None
        if m["lang"] and m["lang"] != "en-IN" and m["to_role"] == "engineer":
            try:
                from . import call
                loc = call.localize(m["text"], m["lang"]); text = "@" + num(m["sender"]) + " " + loc + (("\n" + "\n".join(f"{i}. {b['label']}" for i, b in enumerate(json.loads(m["buttons"]), 1))) if m["buttons"] else "")
                if m["voice"] and len(loc) < 400: ogg = base64.b64encode(voice.to_ogg_opus(voice.tts(loc, m["lang"]))).decode()
            except Exception as e:
                print("wa localize error", repr(e), flush=True)
        c.execute("insert into wa_out(jid,text,mentions,ogg_b64) values(?,?,?,?)", (m["jid"], text, json.dumps(mentions), ogg)); c.execute("update chat_messages set wa_sent=1 where id=?", (m["id"],))
    c.commit(); c.close()


def outbox():
    flush_cases()
    c = db.con(); rows = [dict(r) for r in c.execute("select * from wa_out where sent=0 order by id limit 20")]; c.close()
    return [{"id": r["id"], "jid": r["jid"], "text": r["text"], "mentions": json.loads(r["mentions"] or "[]"), "ogg_b64": r["ogg_b64"]} for r in rows]


def ack(ids):
    c = db.con(); c.executemany("update wa_out set sent=1 where id=?", [(i,) for i in ids]); c.commit(); c.close()
