"""Voice: Sarvam speech-to-text (with language detection) and text-to-speech. Telegram voice is OGG/Opus, browsers record WebM; try as-is, then convert with ffmpeg."""
import base64, os, shutil, subprocess, tempfile
from pathlib import Path
import httpx

LANGS = {"en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada", "ml-IN": "Malayalam",
         "mr-IN": "Marathi", "gu-IN": "Gujarati", "pa-IN": "Punjabi", "od-IN": "Odia"}
H = lambda: {"api-subscription-key": os.environ["SARVAM_API_KEY"]}


def _stt_once(path: Path):
    import mimetypes
    r = httpx.post("https://api.sarvam.ai/speech-to-text", timeout=60, headers=H(), files={"file": (path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "audio/wav")}, data={"model": "saaras:v3"})
    if r.status_code == 402: raise RuntimeError("Sarvam credits exhausted")
    r.raise_for_status(); j = r.json()
    return (j.get("transcript") or "").strip(), j.get("language_code") or "en-IN"


def stt(data: bytes, filename: str = "voice.webm"):
    """Returns (transcript, language_code). The transcript stays in the speaker's own language."""
    d = Path(tempfile.mkdtemp()); src = d / filename; src.write_bytes(data)
    try:
        return _stt_once(src)
    except Exception as first:
        if not shutil.which("ffmpeg"): raise first
        wav = d / "v.wav"; subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "16000", "-ac", "1", str(wav)], check=True, timeout=60)
        return _stt_once(wav)


def to_text(data: bytes, filename: str) -> str:
    return stt(data, filename)[0]


def tts(text: str, lang: str = "en-IN") -> bytes:
    """Speak text in the given language; returns WAV bytes. Falls back to English if the language is not supported."""
    lang = lang if lang in LANGS else "en-IN"
    r = httpx.post("https://api.sarvam.ai/text-to-speech", timeout=60, headers=H(), json={"text": text[:900], "target_language_code": lang, "model": "bulbul:v3"})
    if r.status_code == 402: raise RuntimeError("Sarvam credits exhausted")
    r.raise_for_status()
    return base64.b64decode(r.json()["audios"][0])


def to_ogg_opus(wav: bytes) -> bytes:
    """Telegram voice notes must be OGG/Opus."""
    d = Path(tempfile.mkdtemp()); (d / "a.wav").write_bytes(wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(d / "a.wav"), "-c:a", "libopus", "-b:a", "32k", str(d / "a.ogg")], check=True, timeout=60)
    return (d / "a.ogg").read_bytes()


def to_phone_wav(wav: bytes) -> bytes:
    """Telephone audio is 8 kHz mono; convert so any carrier can play it. Falls back to the original if ffmpeg is missing."""
    if not shutil.which("ffmpeg"): return wav
    d = Path(tempfile.mkdtemp()); (d / "a.wav").write_bytes(wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(d / "a.wav"), "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", str(d / "b.wav")], check=True, timeout=60)
    return (d / "b.wav").read_bytes()
