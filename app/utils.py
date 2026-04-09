from __future__ import annotations

import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.config import settings


def now_local() -> datetime:
    return datetime.now(settings.timezone)


def normalize_external_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif "://" not in raw and raw.startswith("www."):
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    if not netloc and parsed.path and raw.startswith("https://") is False and raw.startswith("http://") is False:
        parsed = urlparse(f"https://{raw}")
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()

    hostname = parsed.hostname.lower() if parsed.hostname else netloc
    port = parsed.port
    if hostname:
        if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname
    normalized = parsed._replace(scheme=scheme, netloc=netloc)
    return urlunparse(normalized)


def canonicalize_url(url: str) -> str:
    parsed = urlparse(normalize_external_url(url))
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not key.startswith("utm_")]
    normalized = parsed._replace(
        scheme=parsed.scheme.lower() or "https",
        netloc=parsed.netloc.lower(),
        fragment="",
        query=urlencode(sorted(query)),
    )
    return urlunparse(normalized)


def extract_domain(url: str) -> str:
    normalized = normalize_external_url(url)
    parsed = urlparse(normalized)
    return (parsed.hostname or parsed.netloc).lower().removeprefix("www.")


def normalize_title(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[^\w\u4e00-\u9fff ]+", "", lowered)
    return lowered.strip()


def make_cluster_key(title: str, domain: str) -> str:
    normalized = normalize_title(title)
    digest = hashlib.sha1(f"{domain}:{normalized[:80]}".encode("utf-8")).hexdigest()
    return digest[:16]


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    cleaned = value.strip()
    try:
        return parsedate_to_datetime(cleaned)
    except Exception:
        pass

    candidates = [
        cleaned,
        cleaned.replace("Z", "+00:00"),
        cleaned.replace("/", "-"),
    ]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def summarize_markdown(markdown: str | None, fallback: str | None = None, limit: int = 240) -> str:
    source = markdown or fallback or ""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", source)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"[#>*`-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def distinct_sections(items: Iterable[str]) -> int:
    return len({item for item in items if item})


def infer_language(*values: str | None) -> str:
    joined = " ".join(value or "" for value in values)
    if re.search(r"[\u4e00-\u9fff]", joined):
        return "zh"
    return "en"
