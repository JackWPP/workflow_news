"""单 URL 直接调用 fetch_via_trafilatura + fetch_via_zhipu_reader, 看到底卡在哪."""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
ENV_PATH = ROOT.parent.parent / ".env"
for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from exp2_summary_value import fetch_via_trafilatura, fetch_via_zhipu_reader  # type: ignore[import-not-found]


async def main():
    urls = [
        "https://example.com/",
        "https://www.compoundingworld.com/",
        "https://m.chinabgao.com/k/zsj/72712.html",
    ]
    for url in urls:
        print(f"\n=== {url} ===")
        s = time.perf_counter()
        traf = await fetch_via_trafilatura(url)
        print(f"  traf: ok={traf['ok']}, n_chars={traf['n_chars']}, dur={traf['duration_ms']}ms, err={traf.get('error')}")
        s2 = time.perf_counter()
        zr = await fetch_via_zhipu_reader(url)
        print(f"  zr: ok={zr['ok']}, n_chars={zr['n_chars']}, dur={zr['duration_ms']}ms, err={zr.get('error')}")
        print(f"  total: {int((time.perf_counter()-s)*1000)}ms")


asyncio.run(main())
