"""Run the LLM extractor over the synthetic quote files and cache results (fixture mode for the deployed demo). Needs OPENROUTER_API_KEY."""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.extract import extract_quote
D = Path(__file__).resolve().parent.parent / "data" / "demo"
jobs = [(f, D / "cache") for f in sorted((D / "quotes").glob("*.pdf"))] + [(f, D / "eval_cache") for f in sorted((D / "eval").glob("*.pdf"))]
def go(j):
    f, out = j; out.mkdir(exist_ok=True)
    r = extract_quote(f); (out / (f.stem + ".json")).write_text(json.dumps(r, indent=1)); return f.name
with ThreadPoolExecutor(6) as ex:
    for n in ex.map(go, jobs): print("extracted", n)
