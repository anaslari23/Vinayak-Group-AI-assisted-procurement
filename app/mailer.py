"""Steps 3 and 6 mail. HARD allowlist: nothing leaves unless every recipient is on a test domain. No real vendor address is ever stored."""
import os, smtplib
from email.message import EmailMessage
from .db import con

ALLOWED_DOMAINS = ("@rfq-test.example", "@vinayak-test.example")
SMTP_HOST, SMTP_PORT = os.getenv("SMTP_HOST", "localhost"), int(os.getenv("SMTP_PORT", "1025"))
HOD_ADDR = "hod@vinayak-test.example"


class RecipientBlocked(Exception):
    pass


def check(addr: str):
    from email.utils import getaddresses
    parsed = getaddresses([addr])
    ok = len(parsed) == 1 and "," not in addr and ";" not in addr and parsed[0][1].count("@") == 1 and \
        "@" + parsed[0][1].split("@")[1].lower() in ALLOWED_DOMAINS and parsed[0][1] == addr.strip()
    if not ok:
        raise RecipientBlocked(f"{addr!r} is not a single address on the test-inbox allowlist {ALLOWED_DOMAINS}")


AGENT_ADDR = "procurement-agent@vinayak-test.example"


def send(kind: str, to: str, subject: str, body: str, frm: str = AGENT_ADDR, attachments: list | None = None) -> str:
    check(to)  # raises before anything is sent or logged as sent
    check(frm)
    msg = EmailMessage(); msg["From"] = frm; msg["To"] = to; msg["Subject"] = subject; msg.set_content(body)
    for path in attachments or []:
        import mimetypes
        mt = (mimetypes.guess_type(str(path))[0] or "application/octet-stream").split("/")
        msg.add_attachment(open(path, "rb").read(), maintype=mt[0], subtype=mt[1], filename=path.name)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=3) as s:
            s.send_message(msg)
        status = "delivered to Mailpit test inbox"
    except OSError:
        status = "logged only (no SMTP server reachable in this environment)"
    c = con(); c.execute("insert into mail_log(kind,to_addr,subject,body,status) values(?,?,?,?,?)", (kind, to, subject, body, status)); c.commit(); c.close()
    return status
