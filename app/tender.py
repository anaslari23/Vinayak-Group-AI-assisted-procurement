"""The tender (what the developer asked vendors to quote). Loaded from DATA_DIR/tender.json.
Default DATA_DIR is data/demo (SYNTHETIC package). data/private (gitignored) holds the real data pack for local use."""
import json, os
from pathlib import Path

SQM_PER_SQFT = 0.09290304
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data" / "demo")))
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR
TENDER = json.loads((DATA_DIR / "tender.json").read_text())
