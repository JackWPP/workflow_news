"""Seed WeChat article URLs for the lab report pipeline.

Usage:
    python scripts/seed_wechat_urls.py "https://mp.weixin.qq.com/s/xxxxx" "https://mp.weixin.qq.com/s/yyyyy"
    python scripts/seed_wechat_urls.py --file urls.txt
"""

import sys

sys.path.insert(0, ".")

from app.database import session_scope
from app.models import WeChatArticle
from app.utils import canonicalize_url


def seed_urls(urls: list[str]) -> int:
    added = 0
    with session_scope() as session:
        for url in urls:
            url = url.strip()
            if not url or "weixin" not in url:
                continue
            normalized = canonicalize_url(url)
            existing = session.scalar(
                __import__("sqlalchemy").select(WeChatArticle).where(WeChatArticle.url == normalized)
            )
            if existing:
                print(f"  [skip] already exists: {url[:60]}")
                continue
            wa = WeChatArticle(
                url=normalized,
                title=url.split("/")[-1][:200] or "待爬取",
                account_name="英蓝云展",
                scrape_status="pending",
            )
            session.add(wa)
            added += 1
            print(f"  [added] {url[:80]}")
        session.commit()
    return added


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/seed_wechat_urls.py <url1> [url2] ...")
        print("       python scripts/seed_wechat_urls.py --file urls.txt")
        sys.exit(1)

    if sys.argv[1] == "--file":
        with open(sys.argv[2], encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        urls = sys.argv[1:]

    print(f"Seeding {len(urls)} WeChat URLs...")
    count = seed_urls(urls)
    print(f"Added {count} new URLs. Run the ingester to scrape them.")
