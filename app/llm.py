"""OpenRouter client. Key is read server-side from env only."""
import base64, json, os, re, httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-5.5")
URL = "https://openrouter.ai/api/v1/chat/completions"


def _file_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    if path.suffix.lower() == ".pdf":
        return {"type": "file", "file": {"filename": path.name, "file_data": f"data:application/pdf;base64,{b64}"}}
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def ask_json(prompt: str, files: list[Path] = (), system: str = "", max_tokens: int = 6000) -> dict:
    content = [{"type": "text", "text": prompt}] + [_file_part(f) for f in files]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    r = httpx.post(URL, timeout=180, headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                   json={"model": MODEL, "messages": msgs, "max_tokens": max_tokens, "temperature": 0})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def ask_text(prompt: str, max_tokens: int = 800) -> str:
    r = httpx.post(URL, timeout=120, headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                   json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0.2})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()
