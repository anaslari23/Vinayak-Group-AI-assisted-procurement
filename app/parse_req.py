"""Step 1: free text / WhatsApp message / voice note -> structured requirement. Sarvam does speech-to-text; Claude structures."""
import mimetypes, os, httpx
from pathlib import Path
from .llm import ask_json

PROMPT = """A site engineer at a Kolkata real-estate developer sent this material/work requirement (it may be informal, Hinglish or Bengali-English).
Return one JSON object only:
{"item_description": str, "category": "waterproofing"|"road/civil"|"other", "project": str|null, "location": str|null,
 "quantity": number|null, "uom": str|null, "needed_by": str|null,
 "missing": [str]}   // 'missing' lists what a procurement HOD would have to ask back (quantity, unit, location, date...)
Do not invent values; use null when absent.

MESSAGE:
"""


def parse(text: str) -> dict:
    return ask_json(PROMPT + text, max_tokens=800)


def transcribe(audio: Path) -> str:
    """Sarvam speech-to-text (REST clips are limited to ~30 s)."""
    r = httpx.post("https://api.sarvam.ai/speech-to-text", timeout=60,
                   headers={"api-subscription-key": os.environ["SARVAM_API_KEY"]},
                   files={"file": (audio.name, audio.read_bytes(), mimetypes.guess_type(audio.name)[0] or "audio/wav")}, data={"model": "saaras:v3"})
    if r.status_code == 402:
        raise RuntimeError("Sarvam credits exhausted")
    r.raise_for_status()
    j = r.json()
    return j.get("transcript") or j.get("text") or ""
