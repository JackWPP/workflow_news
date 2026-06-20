"""Debug exp1 data."""
import json
from pathlib import Path

data = json.loads((Path("experiments/search_v2/results/exp1_raw.json")).read_text(encoding="utf-8"))
rows = data["rows"]
print(f"total rows: {len(rows)}")

if rows:
    r = rows[0]
    print(f"first row keys: {list(r.keys())[:10]}")
    print(f"first row query: {r.get('query')}")
    print(f"first row n_results: {r.get('n_results')}")
    raw = r.get("raw_results") or r.get("raw_results_truncated") or []
    print(f"first row raw_results count: {len(raw)}")

filtered = [r for r in rows if r.get("param_count") == 50 and r.get("param_summary") is True and r.get("param_freshness") == "oneWeek"]
print(f"\ncount=50 summary=true oneWeek: {len(filtered)} rows")
if filtered:
    for r in filtered[:3]:
        raw = r.get("raw_results") or r.get("raw_results_truncated") or []
        print(f"  q={r.get('query','')[:20]:<20s} n={r.get('n_results',0)} raw_len={len(raw)}")
