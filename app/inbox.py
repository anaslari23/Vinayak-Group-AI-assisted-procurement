"""Inbox watcher: polls the Mailpit test inbox for vendor emails addressed to the agent and routes them into the right case.
A reply is tied to its case by the [Case #n] tag in the subject; the vendor is read from the sender (rfq+<vendor>@rfq-test.example)."""
import os, re
from pathlib import Path
import httpx
from . import db, engine, mailer
from .tender import ROOT

MAILPIT = os.getenv("MAILPIT_URL", "http://localhost:8025")
INBOX = ROOT / "data" / "inbox"


def poll():
    try:
        msgs = httpx.get(f"{MAILPIT}/api/v1/messages?limit=100", timeout=3).json()["messages"]
    except Exception:
        return  # no Mailpit here (e.g. a hosted demo): vendor replies are injected directly instead
    c = db.con()
    for m in reversed(msgs):
        if c.execute("select 1 from inbox_seen where id=?", (m["ID"],)).fetchone(): continue
        c.execute("insert into inbox_seen values(?)", (m["ID"],)); c.commit()
        if mailer.AGENT_ADDR not in [a["Address"] for a in m["To"]]: continue
        tag = re.search(r"\[Case #(\d+)\]", m["Subject"]); frm = m["From"]["Address"]
        if not tag or "+" not in frm: continue
        cid, key = int(tag.group(1)), frm.split("+")[1].split("@")[0]
        detail = httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=10).json()
        try:
            atts = detail.get("Attachments") or []
            if atts:
                for a in atts:
                    data = httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}/part/{a['PartID']}", timeout=20).content
                    d = INBOX / m["ID"]; d.mkdir(parents=True, exist_ok=True); path = d / a["FileName"]; path.write_bytes(data)
                    engine.on_vendor_reply(cid, path, key)
            else:
                engine.on_vendor_text_reply(cid, key, detail.get("Text", ""))
        except Exception as e:
            engine.log(cid, "system", "error", f"Inbox: could not process email from {frm}: {type(e).__name__}: {e}")
    c.close()
