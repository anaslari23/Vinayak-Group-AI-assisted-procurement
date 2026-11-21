"""Step 6: recommendation + HOD approval. The agent RECOMMENDS only. There is no order, award or payment code path anywhere."""
import os, re
from itsdangerous import URLSafeTimedSerializer, BadSignature
from .llm import ask_text
from .scoring import reasons

_ser = URLSafeTimedSerializer(os.getenv("SECRET_KEY", "dev-only-change-me"))


def sign(tender: str, vendor: str) -> str:
    return _ser.dumps({"t": tender, "v": vendor})


def verify(token: str, max_age=7 * 86400):
    try:
        return _ser.loads(token, max_age=max_age)
    except BadSignature:
        return None


def facts(rows: list[dict]) -> str:
    out = []
    for r in rows:
        n = r["n"]
        out.append(f"#{r['rank']} {n['vendor']}: score {r['total']:.1f}/100 (price {r['price_pts']:.1f}, performance {r['perf_pts']:.1f}, terms {r['terms_pts']:.1f}); landed Rs {n['landed']:,.0f}; "
                   f"price rank {r['price_rank']}. " + " ".join(reasons(r, rows)))
    return "\n".join(out)


def summary(rows: list[dict]) -> tuple[str, str]:
    """Plain-words summary written by the LLM from computed facts. Falls back to a template if it cites a number not in the facts."""
    f = facts(rows)
    top = rows[0]
    fallback = (f"Recommended for HOD review: {top['n']['vendor']} (score {top['total']:.1f}/100, landed Rs {top['n']['landed']:,.0f}). "
                f"This is a recommendation only; the decision is yours. See the table for each vendor's reasons.")
    try:
        txt = ask_text("Write a 4-6 sentence plain-English note to a procurement HOD recommending a vendor, using ONLY the facts below. "
                       "Do not introduce any number, vendor or claim that is not in the facts. Say clearly that this is a recommendation and the HOD decides. "
                       "Mention the biggest risk and the closest alternative.\n\nFACTS:\n" + f)
        allowed = set(re.findall(r"\d[\d,\.]*", f))
        bad = [x for x in re.findall(r"\d[\d,\.]*", txt) if x.rstrip(".,") not in {a.rstrip(".,") for a in allowed}]
        return (txt, "AI-written from computed numbers") if not bad else (fallback, "template (AI text cited numbers not in the data)")
    except Exception:
        return fallback, "template (AI unavailable)"
