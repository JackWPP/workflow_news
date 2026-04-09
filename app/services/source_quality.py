from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.utils import extract_domain, normalize_external_url

SOURCE_TIER_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
SOURCE_RELIABILITY_LABEL = {
    "A": "高（规则判定）",
    "B": "中高（规则判定）",
    "C": "中（仅可辅助参考）",
    "D": "低（不纳入）",
}

TOP_INDUSTRY_MEDIA = {
    "plasticsnews.com",
    "ptonline.com",
    "plasticstoday.com",
    "europeanplasticsnews.com",
}
ACADEMIC_DOMAINS = {
    "mdpi.com",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
}
MAINSTREAM_MEDIA = {
    "finance.sina.com.cn",
    "tribuneindia.com",
    "hindustantimes.com",
}
MAINSTREAM_MEDIA_SUFFIXES = (
    ".sina.com.cn",
    ".china.com.cn",
)
VERTICAL_MEDIA = {
    "f3dp.cn",
}
OFFICIAL_NEWSROOM_DOMAINS = {
    "clariant.com",
    "www.clariant.com",
}
TECH_BLOG_DOMAINS = {
    "blog.csdn.net",
    "csdn.net",
    "admin5.com",
}
CONTENT_PLATFORM_DOMAINS = {
    "toutiao.com",
    "qq.com",
    "so.html5.qq.com",
}
LOW_VALUE_DOMAINS = {
    "taobao.com": "ecommerce",
    "mobile-phone.taobao.com": "ecommerce",
    "yiqi.com": "aggregator",
    "zhuansushijie.com": "aggregator",
    "marketsandmarkets.com": "marketing",
}

NEWS_PATH_PARTS = ("news", "press", "media", "announcement", "announcements", "corporate")
PRODUCT_PATH_PARTS = ("product", "products", "service", "services", "solutions", "catalog", "chanpin")
ABOUT_PATH_PARTS = ("about", "company", "profile", "introduction")
DOWNLOAD_HINTS = ("download", "attachment", "export", ".pdf")
SEARCH_HINTS = ("search", "query", "keyword", "tag")
PRICE_HINTS = ("price", "quote", "qihuo", "futures", "jiage", "detail/pp", "/pe/", "/pom")
NEWSROOM_HINTS = (
    "/corporate/news",
    "/company/news",
    "/newsroom",
    "/press-release",
    "/press/",
    "/media/",
    "/media-center",
    "/media-centre",
    "/media-releases",
    "/announcements/",
    "/investor/news",
)
ANTI_BOT_PATTERNS = (
    "captcha",
    "验证码",
    "验证",
    "access denied",
    "robot check",
    "security verification",
    "unavailable for legal reasons",
)
NUMERIC_FACT_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:%|亿|万|kg|吨|t|亿元|亿美元|million|billion))", re.IGNORECASE)
PRICE_CONTEXT_RE = re.compile(r"(涨|跌|均价|报价|现货|期货|供需|价差|库存|成本|开工|price|demand|supply)", re.IGNORECASE)
DATE_RE = re.compile(r"(20\d{2}[-/年.]\d{1,2}[-/月.]\d{1,2}日?)")
MATERIAL_RE = re.compile(r"(pp|pe|pvc|pet|pom|abs|pla|pha|树脂|聚丙烯|聚乙烯|聚甲醛|工程塑料|原料)", re.IGNORECASE)
PRICE_MOVE_RE = re.compile(r"(上涨|下跌|走高|走低|波动|回落|上调|下调|涨|跌|increase|decrease|rose|fell)", re.IGNORECASE)


def classify_source(
    *,
    url: str,
    title: str = "",
    content: str = "",
) -> dict[str, Any]:
    domain = extract_domain(url)
    page_kind = detect_page_kind(url, title=title, content=content)
    source_kind = detect_source_kind(domain, page_kind, url=url, title=title, content=content)
    source_tier = infer_source_tier(domain, page_kind, source_kind, url=url)
    is_primary_source = source_kind in {"government", "academic_journal", "official_company_newsroom", "standards"}
    supports_numeric_claims = source_tier in {"A", "B"} and contains_numeric_facts(f"{title}\n{content}")
    evidence_strength = infer_evidence_strength(source_tier, page_kind, is_primary_source)
    allowed_for_trend_summary = source_tier in {"A", "B"} and page_kind not in {"price_snapshot", "download", "anti_bot"}
    requires_observation_only = source_tier == "C"
    publish_block_reason = None

    if source_tier == "D":
        publish_block_reason = "低可信来源或页面类型，不纳入日报"
    elif page_kind == "price_snapshot" and not is_valid_price_content(title, content):
        publish_block_reason = "价格快照页缺少上下文分析，不纳入日报"
    elif page_kind in {"download", "anti_bot", "binary", "navigation", "homepage"}:
        publish_block_reason = "页面类型不适合直接作为日报证据"

    return {
        "domain": domain,
        "page_kind": page_kind,
        "source_kind": source_kind,
        "source_tier": source_tier,
        "source_reliability_label": SOURCE_RELIABILITY_LABEL[source_tier],
        "is_primary_source": is_primary_source,
        "supports_numeric_claims": supports_numeric_claims,
        "evidence_strength": evidence_strength,
        "allowed_for_trend_summary": allowed_for_trend_summary,
        "requires_observation_only": requires_observation_only,
        "publish_block_reason": publish_block_reason,
    }


def detect_page_kind(url: str, title: str = "", content: str = "") -> str:
    normalized_url = normalize_external_url(url)
    parsed = urlparse(normalized_url)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    title_lower = title.lower()
    content_head = (content or "")[:1200].lower()
    url_signals = f"{path} {query} {title_lower}"
    content_signals = f"{title_lower} {content_head}"

    if parse_qs(parsed.query).get("type") == ["11"]:
        return "download"
    if path.endswith(".pdf"):
        return "download"
    if any(hint in path for hint in DOWNLOAD_HINTS) or any(hint in query for hint in DOWNLOAD_HINTS):
        return "download"
    if any(pattern in content_signals for pattern in ANTI_BOT_PATTERNS):
        return "anti_bot"
    if any(hint in url_signals for hint in SEARCH_HINTS):
        return "search"
    if any(hint in path for hint in PRODUCT_PATH_PARTS):
        return "product"
    if any(hint in path for hint in ABOUT_PATH_PARTS):
        return "about"
    if any(hint in url_signals for hint in PRICE_HINTS):
        return "price_snapshot"
    if path in {"", "/"}:
        return "homepage"
    if any(part in path for part in NEWS_PATH_PARTS) or "/article/" in path or "/articles/" in path:
        return "news"
    if looks_like_navigation_page(title_lower, content_head):
        return "navigation"
    if looks_like_binary_blob(content):
        return "binary"
    return "article"


def detect_source_kind(domain: str, page_kind: str, *, url: str = "", title: str = "", content: str = "") -> str:
    url_lower = url.lower()
    if domain.endswith(".gov.cn") or domain.endswith(".gov"):
        return "government"
    if domain.endswith(".edu.cn") or domain.endswith(".edu"):
        return "academic"
    if domain in TOP_INDUSTRY_MEDIA:
        return "top_industry_media"
    if domain in ACADEMIC_DOMAINS:
        return "academic_journal"
    if domain in MAINSTREAM_MEDIA:
        return "mainstream_media"
    if (
        page_kind in {"news", "article"}
        and any(domain.endswith(suffix) for suffix in MAINSTREAM_MEDIA_SUFFIXES)
        and domain not in CONTENT_PLATFORM_DOMAINS
        and domain not in TECH_BLOG_DOMAINS
        and domain not in LOW_VALUE_DOMAINS
    ):
        return "mainstream_media"
    if domain in VERTICAL_MEDIA:
        return "vertical_media"
    if domain in TECH_BLOG_DOMAINS or domain.endswith("csdn.net"):
        return "technical_blog"
    if domain in CONTENT_PLATFORM_DOMAINS:
        return "content_platform"
    if domain in LOW_VALUE_DOMAINS:
        return LOW_VALUE_DOMAINS[domain]
    if domain in OFFICIAL_NEWSROOM_DOMAINS:
        return "official_company_newsroom"
    if page_kind == "news" and any(hint in url_lower for hint in NEWSROOM_HINTS):
        return "official_company_newsroom"
    if page_kind in {"product", "about", "homepage"}:
        return "company_site"
    return "general_site"


def infer_source_tier(domain: str, page_kind: str, source_kind: str, *, url: str = "") -> str:
    if page_kind in {"download", "anti_bot", "binary", "search", "navigation", "product", "about", "homepage"}:
        return "D"
    if source_kind in {"ecommerce", "marketing", "aggregator"}:
        return "D"
    if source_kind in {"government", "academic_journal", "top_industry_media"}:
        return "A"
    if source_kind in {"official_company_newsroom", "vertical_media", "mainstream_media", "academic"}:
        return "B"
    if source_kind in {"technical_blog", "content_platform", "general_site", "company_site"}:
        return "C"
    return "C"


def infer_evidence_strength(source_tier: str, page_kind: str, is_primary_source: bool) -> str:
    if source_tier == "A" and is_primary_source:
        return "high"
    if source_tier in {"A", "B"} and page_kind in {"news", "article", "price_snapshot"}:
        return "medium"
    if source_tier == "C":
        return "low"
    return "discard"


def contains_numeric_facts(text: str) -> bool:
    return bool(NUMERIC_FACT_RE.search(text or ""))


def is_valid_price_content(title: str, content: str) -> bool:
    text = f"{title}\n{content}"
    has_number = contains_numeric_facts(text)
    has_context = bool(PRICE_CONTEXT_RE.search(text))
    has_date = bool(DATE_RE.search(text))
    has_material = bool(MATERIAL_RE.search(text))
    has_move = bool(PRICE_MOVE_RE.search(text))
    return has_number and has_context and has_date and has_material and has_move


def looks_like_navigation_page(title: str, content: str) -> bool:
    if not content:
        return False
    nav_terms = ["首页", "上一页", "下一页", "相关阅读", "推荐阅读", "更多内容", "登录", "注册", "频道"]
    hits = sum(1 for term in nav_terms if term in content)
    return hits >= 4 or (title in {"搜索", "资讯", "新闻"} and hits >= 2)


def looks_like_binary_blob(content: str | None) -> bool:
    if not content:
        return False
    head = content[:300]
    if "%pdf" in head.lower():
        return True
    weird = sum(1 for ch in head if ord(ch) < 9 or 13 < ord(ch) < 32)
    return weird > 8
