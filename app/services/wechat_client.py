"""WeChat Official Account API client.

Uses the mp.weixin.qq.com API to fetch article lists from public accounts.
Requires a token obtained from logging into mp.weixin.qq.com.

Usage:
    1. Log in to https://mp.weixin.qq.com
    2. Open browser devtools → Network tab
    3. Find any request to mp.weixin.qq.com and copy the 'token' param and 'Cookie' header
    4. Store them via set_credentials() or the /api/admin/wechat-token endpoint
    5. Run fetch_articles() to get articles
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from app.database import session_scope
from app.models import AppSetting, WeChatArticle
from app.utils import canonicalize_url, now_local

logger = logging.getLogger(__name__)

_WECHAT_API_BASE = "https://mp.weixin.qq.com/cgi-bin"
_CREDENTIALS_KEY = "wechat_mp_credentials"
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def get_credentials() -> dict[str, str] | None:
    with session_scope() as session:
        setting = session.get(AppSetting, _CREDENTIALS_KEY)
        if setting and isinstance(setting.value, dict):
            return setting.value
    return None


def set_credentials(token: str, cookie: str) -> None:
    with session_scope() as session:
        setting = session.get(AppSetting, _CREDENTIALS_KEY)
        if setting:
            setting.value = {"token": token, "cookie": cookie}
        else:
            session.add(AppSetting(key=_CREDENTIALS_KEY, value={"token": token, "cookie": cookie}))
        session.commit()


def clear_credentials() -> None:
    with session_scope() as session:
        setting = session.get(AppSetting, _CREDENTIALS_KEY)
        if setting:
            session.delete(setting)
            session.commit()


async def search_account(keyword: str) -> list[dict[str, Any]]:
    creds = get_credentials()
    if not creds:
        raise RuntimeError("WeChat credentials not configured. Use set_credentials() first.")

    url = f"{_WECHAT_API_BASE}/searchbiz"
    params = {
        "action": "search_biz",
        "begin": 0,
        "count": 10,
        "query": keyword,
        "token": creds["token"],
        "lang": "zh_CN",
        "f": "json",
        "ajax": "1",
    }
    headers = {
        "Cookie": creds["cookie"],
        "User-Agent": random.choice(_USER_AGENTS),
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()

    data = resp.json()
    if data.get("base_resp", {}).get("ret") != 0:
        ret = data["base_resp"]["ret"]
        msg = data["base_resp"].get("err_msg", "")
        raise RuntimeError(f"WeChat API error: ret={ret}, msg={msg}")

    accounts = []
    for item in data.get("list", []):
        accounts.append({
            "fakeid": item.get("fakeid", ""),
            "nickname": item.get("nickname", ""),
            "alias": item.get("alias", ""),
            "round_head_img": item.get("round_head_img", ""),
            "service_type": item.get("service_type", -1),
        })
    return accounts


async def fetch_article_list(fakeid: str, count: int = 5, begin: int = 0) -> list[dict[str, Any]]:
    creds = get_credentials()
    if not creds:
        raise RuntimeError("WeChat credentials not configured.")

    url = f"{_WECHAT_API_BASE}/appmsg"
    params = {
        "action": "list_ex",
        "begin": begin,
        "count": count,
        "fakeid": fakeid,
        "type": "9",
        "token": creds["token"],
        "lang": "zh_CN",
        "f": "json",
        "ajax": "1",
    }
    headers = {
        "Cookie": creds["cookie"],
        "User-Agent": random.choice(_USER_AGENTS),
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()

    data = resp.json()
    ret = data.get("base_resp", {}).get("ret", -1)
    if ret == 200013:
        raise RuntimeError("WeChat API rate limited. Try again later.")
    if ret == 200003:
        raise RuntimeError("WeChat token expired. Please re-authenticate.")
    if ret != 0:
        msg = data["base_resp"].get("err_msg", "")
        raise RuntimeError(f"WeChat API error: ret={ret}, msg={msg}")

    articles = []
    for item in data.get("app_msg_list", []):
        articles.append({
            "aid": item.get("aid", ""),
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "cover": item.get("cover", ""),
            "digest": item.get("digest", ""),
            "create_time": item.get("create_time", 0),
            "update_time": item.get("update_time", 0),
        })
    return articles


async def sync_account_articles(
    fakeid: str,
    account_name: str,
    max_pages: int = 10,
    progress_callback=None,
) -> int:
    """Fetch articles from a WeChat account and store in WeChatArticle table.

    Args:
        max_pages: Max pages to fetch (10 articles per page, so 10 pages = up to 100 articles).
        progress_callback: Optional async callable ``(page, added_so_far, page_articles_count)``
            called after each page is processed.

    Returns the number of new articles added.
    """
    added = 0
    per_page = 10
    for page in range(max_pages):
        try:
            articles = await fetch_article_list(fakeid, count=per_page, begin=page * per_page)
        except RuntimeError as exc:
            logger.warning("WeChat sync page %d failed: %s", page + 1, exc)
            break
        if not articles:
            break

        page_new = 0
        with session_scope() as session:
            sqlalchemy = __import__("sqlalchemy")
            for art in articles:
                link = art.get("link", "")
                if not link:
                    continue
                normalized = canonicalize_url(link)
                existing = session.scalar(
                    sqlalchemy.select(WeChatArticle).where(WeChatArticle.url == normalized)
                )
                if existing:
                    continue

                wa = WeChatArticle(
                    url=normalized,
                    title=art["title"][:1024],
                    account_name=account_name,
                    published_at=_ts_to_datetime(art.get("update_time") or art.get("create_time")),
                    scrape_status="pending",
                    summary=art.get("digest", "")[:200] or None,
                    image_url=art.get("cover") or None,
                )
                session.add(wa)
                added += 1
                page_new += 1
            session.commit()

        logger.info("WeChat sync: page %d for '%s', got %d (%d new)", page + 1, account_name, len(articles), page_new)
        if progress_callback:
            try:
                await progress_callback(page + 1, added, len(articles))
            except Exception:
                pass
        if len(articles) < per_page:
            break
        # Rate limit between pages
        await asyncio.sleep(random.randint(2, 4))

    return added


def _ts_to_datetime(ts: int | None):
    if not ts:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, OSError):
        return None
