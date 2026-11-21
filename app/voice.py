"""Voice note -> text via Sarvam. Telegram voice notes are OGG/Opus, browsers record WebM; try as-is, then convert with ffmpeg if present."""
import shutil, subprocess, tempfile
from pathlib import Path
from .parse_req import transcribe


def to_text(data: bytes, filename: str) -> str:
    d = Path(tempfile.mkdtemp()); src = d / filename; src.write_bytes(data)
    try:
        return transcribe(src)
    except Exception as first:
        if not shutil.which("ffmpeg"):
            raise first
        wav = d / "v.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "16000", "-ac", "1", str(wav)], check=True, timeout=60)
        return transcribe(wav)
