from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.harness import DEFAULT_BLOCKED_DOMAINS
from app.services.source_quality import SOURCE_TIER_RANK, classify_source
from app.services.working_memory import WorkingMemory
from app.utils import canonicalize_url, extract_domain, now_local

_POSITIVE_KEYWORDS = [
    "高分子",
    "塑料",
    "树脂",
    "改性",
    "注塑",
    "挤出",
    "吹塑",
    "复合材料",
    "recycling",
    "polymer",
    "plastics",
    "resin",
    "extrusion",
    "injection",
    "processing",
]
_NEGATIVE_KEYWORDS = [
    "market forecast",
    "cagr",
    "stock",
    "earnings",
    "marathon",
    "football",
    "soccer",
    "war",
    "missile",
    "ophthalmology",
    "biogen",
    "apellis",
    "pharma",
    "财经",
    "股价",
    "财报",
    "马拉松",
    "足球",
    "战争",
    "导弹",
    "医药",
]
_SINGLE_DOMAIN_CANDIDATE_CAP = 3

_NON_ARTICLE_URL_PATTERNS = (
    "/course/",
    "/site/menu/",
    "/list.asp",
    "/firm/",
    "/ask/view/",
    "/ch/reader/view_abstract.aspx",
    "/info/996",
    "/info/detail/",
    "/issue/",
)

_NON_ARTICLE_DOMAIN_SUFFIXES = (
    "zhihuishu.com",
    "qcc.com",
    "11467.com",
    "bohe.cn",
    "100ppi.com",
)

_PREVIEW_REJECT_PAGE_KINDS = {
    "download",
    "search",
    "product",
    "about",
    "homepage",
    "navigation",
    "anti_bot",
    "binary",
}
_NON_RETRYABLE_READ_STATES = {
    "readable",
    "rejected_by_page_kind",
    "rejected_by_recency",
}
_SEARCH_RECENCY_LABEL = "36 小时内"
_TRUSTED_SOURCE_SEED_LIMIT = 4
_TRUSTED_SOURCE_ITEMS_PER_FEED = 2
_TRUSTED_SOURCE_TIER_RANK = {
    "government": 5,
    "standards": 4,
    "academic-journal": 4,
    "top-industry-media": 3,
    "company-newsroom": 2,
    "unknown": 1,
    "pr-wire": 0,
}
_SOURCE_KIND_SCORE_BONUS = {
    "government": 1.4,
    "official_company_newsroom": 1.2,
    "top_industry_media": 1.0,
    "mainstream_media": 0.8,
    "academic_journal": 0.7,
    "vertical_media": 0.6,
    "academic": 0.4,
}
_SECTION_HINT_KEYWORDS = {
    "industry": [
        "注塑",
        "挤出",
        "设备",
        "machine",
        "plant",
        "产能",
        "扩产",
        "工厂",
        "量产",
        "automotive",
        "packaging",
        "medical",
    ],
    "policy": [
        "政策",
        "法规",
        "标准",
        "cbam",
        "epr",
        "限塑",
        "监管",
        "compliance",
        "tariff",
    ],
    "academic": [
        "论文",
        "研究",
        "journal",
        "study",
        "materials science",
        "聚合物",
        "polymer",
        "复合材料",
        "机理",
    ],
}

_CANDIDATE_BLOCKED_DOMAIN_SET = set(DEFAULT_BLOCKED_DOMAINS)


def _is_candidate_blocked_domain(domain: str) -> bool:
    if domain in _CANDIDATE_BLOCKED_DOMAIN_SET:
        return True
    parts = domain.split(".")
    for index in range(1, len(parts)):
        if ".".join(parts[index:]) in _CANDIDATE_BLOCKED_DOMAIN_SET:
            return True
    return False


def _is_non_article_url(url: str) -> bool:
    url_lower = url.lower()
    for pattern in _NON_ARTICLE_URL_PATTERNS:
        if pattern in url_lower:
            return True
    domain = extract_domain(url)
    for suffix in _NON_ARTICLE_DOMAIN_SUFFIXES:
        if domain == suffix or domain.endswith("." + suffix):
            return True
    return False


def candidate_section_hints(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata") or {}
    intended_section = row.get("section") or metadata.get("intended_section")
    if intended_section in _SECTION_HINT_KEYWORDS:
        return {str(intended_section)}
    text = f"{row.get('title', '')} {row.get('snippet', '')}".lower()
    hints: set[str] = set()
    for section, keywords in _SECTION_HINT_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            hints.add(section)
    return hints


def candidate_score(
    row: dict[str, Any],
    memory: WorkingMemory,
    query_usage: dict[str, int],
    quality: dict[str, Any],
) -> float:
    url = row.get("url", "")
    domain = row.get("domain") or extract_domain(url)
    title = row.get("title", "")
    snippet = row.get("snippet", "")
    metadata = row.get("metadata") or {}
    text = f"{title} {snippet}".lower()
    score = 0.0

    pub = row.get("published_at")
    if pub is not None:
        now_utc = (
            now_local().astimezone(pub.tzinfo)
            if getattr(pub, "tzinfo", None)
            else now_local()
        )
        age_days = max(
            (now_utc.replace(tzinfo=None) - pub.replace(tzinfo=None)).days, 0
        )
        if age_days <= 7:
            score += 4.0
        else:
            score -= 3.0
    else:
        score -= 1.5

    score += float(SOURCE_TIER_RANK.get(quality["source_tier"], 1)) * 1.5
    score += _SOURCE_KIND_SCORE_BONUS.get(
        str(quality.get("source_kind") or ""), 0.0
    )
    if (row.get("search_type") or metadata.get("search_type")) == "rss":
        score += 1.8
    if metadata.get("is_direct_source"):
        score += 1.2
    if metadata.get("intended_section"):
        score += 0.8
    if metadata.get("intended_category"):
        score += 0.4
    if str(metadata.get("query_family") or "").startswith("ai_"):
        score += 0.8
    score += min(float(metadata.get("source_priority") or 0) / 50.0, 2.0)
    positive_hits = sum(
        1 for keyword in _POSITIVE_KEYWORDS if keyword.lower() in text
    )
    negative_hits = sum(
        1 for keyword in _NEGATIVE_KEYWORDS if keyword.lower() in text
    )
    if (
        negative_hits > 0
        and positive_hits == 0
        and quality["source_tier"] in {"C", "D"}
    ):
        return -5.0
    score += positive_hits * 0.5
    score -= negative_hits * 3.0
    if (
        negative_hits > 0
        and positive_hits == 0
        and SOURCE_TIER_RANK.get(quality["source_tier"], 1) <= 2
    ):
        score -= 3.0

    for section in candidate_section_hints(row):
        current_count = getattr(memory.coverage, f"{section}_count", 0)
        if section in ("policy", "academic"):
            if current_count <= 0:
                score += 2.0
            elif current_count == 1:
                score += 1.0
        else:
            if current_count <= 0:
                score += 1.3
            elif current_count == 1:
                score += 0.6

    query = memory.url_search_query.get(url, "")
    score += max(0.0, 1.5 - float(query_usage.get(query, 0)))
    return score


def extract_candidates(
    memory: WorkingMemory,
    runtime: dict[str, Any],
    limit: int | None = None,
) -> list[tuple[str, str]]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    domain_counts: dict[str, int] = defaultdict(int)
    query_usage: dict[str, int] = defaultdict(int)
    scored: list[tuple[float, str, str]] = []
    limit = limit if limit is not None else runtime["max_extractions_per_run"]

    for row in memory.search_results:
        url = row.get("url", "")
        if not url:
            memory.record_candidate_rejection("missing_url")
            continue

        normalized_url = canonicalize_url(url)
        if memory.has_read(normalized_url):
            memory.record_candidate_rejection("already_read")
            continue
        if memory.has_attempted_read(normalized_url):
            read_state = memory.get_read_metadata(normalized_url).get(
                "read_state", ""
            )
            if read_state in _NON_RETRYABLE_READ_STATES:
                memory.record_candidate_rejection("already_attempted_non_retryable")
                continue
        if any(
            ext in normalized_url.lower()
            for ext in [".pdf", ".jpg", ".png", ".gif"]
        ):
            memory.record_candidate_rejection("unsupported_extension")
            continue
        if _is_non_article_url(normalized_url):
            memory.record_candidate_rejection("non_article_url_pattern")
            continue
        if normalized_url in seen_urls:
            memory.record_candidate_rejection("duplicate_url")
            continue

        title = row.get("title", "")
        title_key = _normalize_title(title)
        if title_key and title_key in seen_titles:
            memory.record_candidate_rejection("duplicate_title")
            continue

        domain = row.get("domain") or extract_domain(url)
        metadata = row.get("metadata") or {}
        if _is_candidate_blocked_domain(domain):
            memory.record_candidate_rejection("blocked_domain_candidate")
            continue
        domain_failure_record = memory.domain_failures.get(domain)
        if domain_failure_record and domain_failure_record.get("count", 0) >= 2:
            memory.record_candidate_rejection("domain_cooldown")
            continue
        if domain_counts[domain] >= _SINGLE_DOMAIN_CANDIDATE_CAP:
            memory.record_candidate_rejection("domain_candidate_cap")
            continue

        quality = classify_source(
            url=url, title=title, content=row.get("snippet", "")
        )
        if quality["page_kind"] in _PREVIEW_REJECT_PAGE_KINDS:
            memory.record_candidate_rejection(f"page_kind_{quality['page_kind']}")
            continue
        if quality["source_tier"] == "D":
            memory.record_candidate_rejection("low_value_source_tier_d")
            continue
        if quality.get("source_kind") == "content_platform":
            memory.record_candidate_rejection("content_platform_candidate")
            continue
        if (
            row.get("published_at") is None
            and quality["source_tier"] == "C"
            and not metadata.get("is_direct_source")
            and (row.get("search_type") or metadata.get("search_type")) != "rss"
            and not candidate_section_hints(row)
        ):
            memory.record_candidate_rejection("missing_publish_time_low_confidence")
            continue

        score = candidate_score(row, memory, query_usage, quality)
        if score <= -3.0:
            memory.record_candidate_rejection("off_topic_candidate")
            continue

        seen_urls.add(normalized_url)
        if title_key:
            seen_titles.add(title_key)
        domain_counts[domain] += 1
        query = memory.url_search_query.get(url, "")
        if query:
            query_usage[query] += 1
        discovery = []
        if metadata.get("search_query"):
            discovery.append(f"query={metadata.get('search_query')}")
        if metadata.get("intended_section"):
            discovery.append(f"section={metadata.get('intended_section')}")
        if metadata.get("intended_category"):
            discovery.append(f"category={metadata.get('intended_category')}")
        prefix = f"[discovery {'; '.join(discovery)}]\n" if discovery else ""
        context = f"{prefix}{title}\n{row.get('snippet', '')}".strip()
        scored.append((score, url, context))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [(url, context) for _, url, context in scored[:limit]]


def _normalize_title(title: str) -> str:
    from app.utils import normalize_title
    return normalize_title(title)
