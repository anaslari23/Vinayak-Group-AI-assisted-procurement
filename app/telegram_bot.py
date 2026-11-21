"""Telegram front door. Long-polls for updates, routes them into the engine, and delivers the agent's queued chat messages.
One bot, two roles: everyone is a site engineer by default; send /hod once to become the HOD. Buttons are inline keyboards."""
import json, os, threading, time
import httpx
from . import db, engine, voice

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = "https://api.telegram.org"
_stop = threading.Event()


def tg(method, **params):
    r = httpx.post(f"{API}/bot{TOKEN}/{method}", json=params, timeout=40)
    return r.json()


def send(chat_id, text, buttons=None, case_id=None):
    p = {"chat_id": chat_id, "text": text[:4000]}
    if buttons:
        p["reply_markup"] = {"inline_keyboard": [[{"text": b["label"], "callback_data": f"h|{case_id}|{b['data']}"}] for b in buttons]}
    return tg("sendMessage", **p)


def _chat(chat_id):
    c = db.con(); r = c.execute("select * from chats where chat_id=?", (chat_id,)).fetchone(); c.close(); return dict(r) if r else None


def _set_chat(chat_id, role=None, case_id=None, name=None, lang=None, voice_mode=None):
    c = db.con(); cur = _chat(chat_id) or {"role": "engineer", "case_id": None, "name": name, "lang": None, "voice": 0}
    c.execute("insert or replace into chats(chat_id,role,case_id,name,lang,voice) values(?,?,?,?,?,?)", (chat_id, role or cur["role"], case_id if case_id is not None else cur["case_id"], name or cur["name"],
              lang or cur.get("lang"), cur.get("voice", 0) if voice_mode is None else int(voice_mode))); c.commit(); c.close()


def send_voice(chat_id, text, lang):
    """Speak a reply as a Telegram voice note in the caller's language (Sarvam TTS, converted to OGG/Opus)."""
    ogg = voice.to_ogg_opus(voice.tts(text, lang))
    httpx.post(f"{API}/bot{TOKEN}/sendVoice", data={"chat_id": chat_id}, files={"voice": ("reply.ogg", ogg, "audio/ogg")}, timeout=60)


def handle_update(u: dict):
    if "callback_query" in u:
        q = u["callback_query"]; _, cid, action = q["data"].split("|", 2)
        tg("answerCallbackQuery", callback_query_id=q["id"], text="Recorded")
        engine.on_hod_action(int(cid), action); return
    m = u.get("message")
    if not m: return
    chat_id = m["chat"]["id"]; name = (m.get("from") or {}).get("first_name")
    if not _chat(chat_id): _set_chat(chat_id, "engineer", None, name)
    text = m.get("text")
    if text in ("/start", "/help"):
        send(chat_id, "Vinayak procurement agent. Send me a site requirement as text or a voice note and I will handle the rest: questions, item code, RFQs, quotes, comparison. A human (the HOD) makes the final call.\n\nSend /hod to register this chat as the HOD."); return
    if text == "/hod":
        _set_chat(chat_id, "hod"); send(chat_id, "This chat is now the HOD. I will send you item-code questions and vendor recommendations with Approve / Send back / Reject buttons. Nothing is ordered or paid by me."); return
    if text == "/engineer":
        _set_chat(chat_id, "engineer"); send(chat_id, "This chat is now a site engineer."); return
    me = _chat(chat_id)
    if me["role"] == "hod":
        if text: send(chat_id, "Use the buttons on my recommendation messages. (Text is not needed on the HOD side.)")
        return
    if m.get("voice") or m.get("audio"):
        f = m.get("voice") or m.get("audio")
        try:
            path = tg("getFile", file_id=f["file_id"])["result"]["file_path"]
            data = httpx.get(f"{API}/file/bot{TOKEN}/{path}", timeout=40).content
            text, lang = voice.stt(data, "voice.ogg")
            _set_chat(chat_id, lang=lang, voice_mode=True)
            send(chat_id, f"({voice.LANGS.get(lang, lang)}) I heard: \"{text}\"")
        except Exception as e:
            send(chat_id, f"Sorry, I could not read that voice note ({type(e).__name__}). Please type it."); return
    if not text: return
    was_voice = bool(m.get("voice") or m.get("audio"))
    if not was_voice: _set_chat(chat_id, voice_mode=False)
    cid = engine.engineer_inbound(text, "telegram (voice)" if was_voice else "telegram", (me or {}).get("case_id"))
    _set_chat(chat_id, case_id=cid)


def deliver():
    """Push queued agent messages to Telegram. Engineer messages go to the chat that owns the case; HOD messages to the registered HOD chat."""
    c = db.con(); pending = [dict(r) for r in c.execute("select * from chat_messages where sent=0 order by id")]
    hod = c.execute("select chat_id from chats where role='hod' order by rowid desc limit 1").fetchone()
    wa = {r[0] for r in c.execute('select case_id from wa_cases')}
    for m in pending:
        if m["case_id"] in wa:   # WhatsApp cases are delivered by the WhatsApp bridge
            c.execute('update chat_messages set sent=2 where id=?', (m['id'],)); continue
        target = None
        if m["to_role"] == "hod" and hod: target = hod["chat_id"]
        elif m["to_role"] == "engineer":
            r = c.execute("select chat_id from chats where case_id=? and role='engineer'", (m["case_id"],)).fetchone()
            if r: target = r["chat_id"]
            elif engine._case(m["case_id"])["data"].get("channel") != "telegram":
                c.execute("update chat_messages set sent=2 where id=?", (m["id"],)); continue  # web-created case: simulator only
        if target is None: continue
        text, spoken = m["text"], None
        ch = c.execute("select lang, voice from chats where chat_id=?", (target,)).fetchone()
        if m["to_role"] == "engineer" and ch and ch["voice"] and ch["lang"]:   # they spoke to us: answer in their language, text and voice
            try:
                from . import call
                text = call.localize(m["text"], ch["lang"]); spoken = (text, ch["lang"])
            except Exception as e:
                print("telegram localize error:", repr(e), flush=True)
        resp = send(target, text, json.loads(m["buttons"]) if m["buttons"] else None, m["case_id"])
        if resp.get("ok"): c.execute("update chat_messages set sent=1 where id=?", (m["id"],))
        if spoken and resp.get("ok"):
            try: send_voice(target, *spoken)
            except Exception as e: print("telegram voice error:", repr(e), flush=True)
    c.commit(); c.close()


def _poll():
    offset = 0
    while not _stop.is_set():
        try:
            r = tg("getUpdates", offset=offset, timeout=25)
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                print("telegram update:", json.dumps(u)[:200], flush=True)
                try: handle_update(u)
                except Exception as e: print("telegram handler error:", repr(e), flush=True)
        except Exception as e:
            print("telegram poll error:", repr(e), flush=True); time.sleep(3)


def _deliver_loop():
    while not _stop.is_set():
        try: deliver()
        except Exception as e: print("telegram deliver error:", e)
        time.sleep(1.5)


def start():
    if not TOKEN: return "no TELEGRAM_BOT_TOKEN set: Telegram front door off"
    me = tg("getMe")
    if not me.get("ok"): return f"Telegram token rejected ({me.get('description')}): Telegram front door off"
    tg("deleteWebhook")
    threading.Thread(target=_poll, daemon=True).start(); threading.Thread(target=_deliver_loop, daemon=True).start()
    return f"Telegram bot @{me['result']['username']} running"
