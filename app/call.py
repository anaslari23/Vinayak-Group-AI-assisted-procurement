"""The calling agent: a spoken, multilingual front door. A caller speaks in any supported Indian language; the agent detects it,
understands it (Claude), asks what is missing, reads the requirement back for confirmation, and only then lets the case continue.
The agent's reply is made short and spoken, translated into the caller's language and voiced with Sarvam.
A real phone number plugs into turn() through the telephony webhook in main.py (needs a provider account; untested without one)."""
import uuid
from functools import lru_cache
from . import db, engine, voice
from .llm import ask_text

SESS: dict = {}
WAITING_STATES = ("NEW", "CLARIFY", "MATCH_ASK", "CONFIRM")


@lru_cache(maxsize=512)
def localize(text: str, lang: str) -> str:
    """Shorten for the phone and translate into the caller's language (site-engineer register; Hinglish for Hindi). Numbers, codes and units are kept."""
    name = voice.LANGS.get(lang, "English")
    style = "simple spoken Hinglish as site engineers speak: Hindi in Devanagari mixing common English nouns" if lang == "hi-IN" else f"simple, polite spoken {name}"
    return ask_text(f"Rewrite this message from a procurement assistant for a PHONE CALL to a site engineer. At most 3 short sentences. Language: {style}. "
                    f"Keep numbers, item codes, units and names exactly. Output only the spoken text, no quotes.\n\nMESSAGE:\n{text}", max_tokens=300)


def _new_msgs(cid: int, after_id: int):
    c = db.con(); rows = [dict(r) for r in c.execute("select id, text from chat_messages where case_id=? and to_role='engineer' and id>? order by id", (cid, after_id))]; c.close(); return rows


def _closing(case):
    st, w = case["state"], case["waiting"]
    if w == "hod": return "I have sent this to the back office for a decision. You will get a message here."
    if st in ("RFQ", "QUOTING", "STOCK", "COMPARE", "AWAITING_HOD"): return "Everything is confirmed and the next steps are running. You do not need to do anything."
    return ""


def turn(call_id: str | None, audio: bytes, filename: str = "voice.webm") -> dict:
    text, lang = voice.stt(audio, filename)
    s = SESS.setdefault(call_id or uuid.uuid4().hex[:10], {"case": None, "last": 0, "lang": lang, "log": []})
    cid_key = next(k for k, v in SESS.items() if v is s)
    s["lang"] = lang
    if not text:
        reply_en = "Sorry, I could not hear that. Please say it again."
        return _speak(cid_key, s, "", lang, reply_en, False)
    s["log"].append(("you", text, lang))
    cid = engine.engineer_inbound(text, "call (voice)", s["case"])
    s["case"] = cid
    case = engine._case(cid); msgs = _new_msgs(cid, s["last"]); s["last"] = msgs[-1]["id"] if msgs else s["last"]
    done = not (case["state"] in WAITING_STATES and case["waiting"] == "engineer")
    reply_en = " ".join(m["text"] for m in msgs)
    if done:
        reply_en = (reply_en + " " + _closing(case)).strip()
    return _speak(cid_key, s, text, lang, reply_en or "Okay.", done)


def _speak(call_id, s, heard, lang, reply_en, done):
    local = localize(reply_en, lang) if lang != "en-IN" else localize(reply_en, "en-IN")
    wav = voice.tts(local, lang)
    s["log"].append(("agent", local, lang))
    return {"call_id": call_id, "case_id": s["case"], "heard": heard, "lang": lang, "lang_name": voice.LANGS.get(lang, lang), "reply_en": reply_en, "reply": local, "wav": wav, "done": done}
