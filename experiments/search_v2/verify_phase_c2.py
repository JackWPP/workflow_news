"""验证 Phase C.2 智谱 reader 兜底."""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from app.services.zhipu_reader import ZhipuReaderClient
from app.services.scraper import ScraperClient


async def main():
    print("=== Phase C.2: ZhipuReader + ScraperClient 验证 ===")

    # Test 1: ZhipuReaderClient 直接调用
    print()
    print("--- Test 1: ZhipuReaderClient direct ---")
    reader = ZhipuReaderClient()
    print(f"  enabled: {reader.enabled}")

    test_url = "https://example.com/"
    print(f"  scraping: {test_url}")
    result = await reader.scrape(test_url, timeout_seconds=20)
    print(f"  status: {result['status']}")
    print(f"  title: {result.get('title', '')[:60]}")
    print(f"  markdown_len: {len(result.get('markdown', ''))}")
    print(f"  image_url: {result.get('image_url')}")
    print(f"  scrape_layer: {result.get('scrape_layer')}")

    if result["status"] == "ok" and len(result.get("markdown", "")) > 0:
        print("  [PASS] ZhipuReader direct")
    else:
        print(f"  [FAIL] ZhipuReader direct - {result.get('error')}")

    # Test 2: ScraperClient 含 zhipu_reader
    print()
    print("--- Test 2: ScraperClient with zhipu_reader ---")
    scraper = ScraperClient()
    print(f"  enabled: {scraper.enabled}")
    print(f"  has _zhipu_reader: {hasattr(scraper, '_zhipu_reader')}")
    print(f"  _zhipu_reader.enabled: {scraper._zhipu_reader.enabled if hasattr(scraper, '_zhipu_reader') else 'N/A'}")

    # Test 3: ScraperClient 完整抓取
    print()
    print("--- Test 3: ScraperClient full scrape ---")
    test_url2 = "https://www.compoundingworld.com/"
    print(f"  scraping: {test_url2}")
    result2 = await scraper.scrape(test_url2, timeout_seconds=25)
    print(f"  status: {result2.get('status')}")
    print(f"  title: {result2.get('title', '')[:60]}")
    print(f"  markdown_len: {len(result2.get('markdown', ''))}")
    print(f"  scrape_layer: {result2.get('scrape_layer', 'unknown')}")

    if result2.get("status") != "error" and len(result2.get("markdown", "")) > 0:
        print("  [PASS] ScraperClient full scrape")
    else:
        print(f"  [FAIL] ScraperClient full scrape - {result2.get('error')}")

    # Test 4: health_snapshot
    print()
    print("--- Test 4: ZhipuReader health_snapshot ---")
    snap = reader.health_snapshot()
    for k, v in snap.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
