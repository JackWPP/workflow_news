"""验证 bocha_search.py Phase 0.5 改动."""
import asyncio
import os
import sys
from pathlib import Path

# Load .env
ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from app.services.bocha_search import BochaSearchClient


async def main():
    client = BochaSearchClient()
    print(f"enabled: {client.enabled}")
    print()
    print("=== health_snapshot before any request ===")
    snap_before = client.health_snapshot()
    for k, v in snap_before.items():
        print(f"  {k}: {v}")

    print()
    print("=== Running 1 search ===")
    results = await client.search("注塑机 新品发布", count=10, summary=True)
    print(f"results: {len(results)}")

    print()
    print("=== health_snapshot after 1 request ===")
    snap_after = client.health_snapshot()
    for k, v in snap_after.items():
        print(f"  {k}: {v}")

    # 验证关键字段
    print()
    new_fields = ["avg_results_per_query", "p50_summary_chars", "p95_summary_chars",
                  "p50_latency_ms", "p95_latency_ms", "total_results"]
    print("=== V2 Phase 0.5 new fields verification ===")
    for f in new_fields:
        present = f in snap_after
        value = snap_after.get(f)
        print(f"  {f}: present={present}, value={value}")
    all_ok = all(f in snap_after for f in new_fields)
    print(f"\nALL NEW FIELDS PRESENT: {all_ok}")


if __name__ == "__main__":
    asyncio.run(main())
