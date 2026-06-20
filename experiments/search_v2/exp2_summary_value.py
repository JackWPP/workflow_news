"""实验 2 (改进版): 内容获取四方对比 + 智谱 URL 质量验证

四方对比 (从样本池里取):
- A. Bocha 800 字 summary  (零额外调用, 来自 exp1)
- B. 智谱 sogou content     (零额外调用, 来自 exp4_ext)
- C. Trafilatura 直抓        (HTTP + trafilatura, 是项目 ScraperClient 的 direct 路径)
- D. 智谱 reader API         (POST /api/paas/v4/reader)

样本设计:
- 从 exp1 拿 Bocha 的 30 条 URL (5 条/query × 6 query)
- 从 exp4_ext 拿智谱 sogou 的 30 条 URL (5 条/query × 6 query)
- 总共 60 条 URL, 各 URL 跑 C+D 两个抓取实测

健壮性:
- 单 URL hard timeout = 15s
- 任务级 asyncio.wait_for 兜底
- 任何一方失败不阻塞另一方

输出:
- results/exp2_raw.json
- results/exp2_summary.csv
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ZHIPU_KEY = os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_READER_URL = "https://open.bigmodel.cn/api/paas/v4/reader"

RESULTS_DIR = ROOT / "results"
REPORTS_DIR = ROOT / "reports"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PER_URL_HARD_TIMEOUT = 25.0
N_PER_QUERY_PER_SOURCE = 5
CONCURRENCY = 3


def pick_urls():
    """从 exp1 + exp4_ext 各取 30 条, 标记来源."""
    urls = []
    seen = set()

    exp1 = json.loads((RESULTS_DIR / "exp1_raw.json").read_text(encoding="utf-8"))
    for q in dict.fromkeys(r["query"] for r in exp1["rows"]):
        candidates = [r for r in exp1["rows"]
                      if r["query"] == q and r["param_count"] == 50
                      and r["param_summary"] is True and r["param_freshness"] == "oneWeek"
                      and r["ok"]]
        if not candidates:
            continue
        raw = candidates[0].get("raw_results") or []
        picked = 0
        for item in raw:
            url = item.get("url") or ""
            if not url or url in seen:
                continue
            urls.append({
                "url": url, "source": "bocha", "query": q,
                "language": candidates[0]["language"],
                "section": candidates[0]["section"],
                "title": item.get("name") or "",
                "ref_chars": len(item.get("summary") or ""),
                "ref_published": item.get("datePublished") or "",
                "ref_image": item.get("thumbnailUrl") or "",
            })
            seen.add(url)
            picked += 1
            if picked >= N_PER_QUERY_PER_SOURCE:
                break

    exp4 = json.loads((RESULTS_DIR / "exp4_ext_raw.json").read_text(encoding="utf-8"))
    for q in dict.fromkeys(r["query"] for r in exp4["rows"]):
        candidates = [r for r in exp4["rows"]
                      if r["query"] == q and r["param_count"] == 50
                      and r["param_freshness"] == "oneWeek" and r["ok"]]
        if not candidates:
            continue
        raw = candidates[0].get("raw_results") or []
        picked = 0
        for item in raw:
            url = (item.get("link") or "").strip()
            if not url or url in seen:
                continue
            urls.append({
                "url": url, "source": "zhipu_sogou", "query": q,
                "language": candidates[0]["language"],
                "section": candidates[0]["section"],
                "title": item.get("title") or "",
                "ref_chars": len(item.get("content") or ""),
                "ref_published": item.get("publish_date") or "",
                "ref_image": item.get("icon") or "",
            })
            seen.add(url)
            picked += 1
            if picked >= N_PER_QUERY_PER_SOURCE:
                break
    return urls


async def fetch_via_trafilatura(url):
    import httpx
    import trafilatura
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=PER_URL_HARD_TIMEOUT, follow_redirects=True,
                                      headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = await client.get(url)
            html = resp.text
            status = resp.status_code
        if status >= 400:
            return {"ok": False, "error": f"http_{status}",
                    "duration_ms": int((time.perf_counter()-start)*1000),
                    "n_chars": 0, "title": "", "published_at": "", "image_url": ""}
        text = trafilatura.extract(html, include_comments=False, include_tables=False,
                                    no_fallback=False, favor_recall=True) or ""
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta else "") or ""
        published_at = (meta.date if meta else "") or ""
        image_url = (meta.image if meta else "") or ""
        return {"ok": True, "error": None,
                "duration_ms": int((time.perf_counter()-start)*1000),
                "n_chars": len(text), "title": title,
                "published_at": published_at, "image_url": image_url}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "duration_ms": int((time.perf_counter()-start)*1000),
                "n_chars": 0, "title": "", "published_at": "", "image_url": ""}


async def fetch_via_zhipu_reader(url):
    import httpx
    if not ZHIPU_KEY:
        return {"ok": False, "error": "no_key", "duration_ms": 0,
                "n_chars": 0, "title": "", "published_at": "", "image_url": ""}
    start = time.perf_counter()
    payload = {"url": url, "timeout": 12, "no_cache": False, "return_format": "markdown"}
    headers = {"Authorization": f"Bearer {ZHIPU_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=PER_URL_HARD_TIMEOUT) as client:
            resp = await client.post(ZHIPU_READER_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            return {"ok": False, "error": f"http_{resp.status_code}: {resp.text[:200]}",
                    "duration_ms": int((time.perf_counter()-start)*1000),
                    "n_chars": 0, "title": "", "published_at": "", "image_url": ""}
        body = resp.json()
        rr = body.get("reader_result") or {}
        content = rr.get("content") or ""
        title = rr.get("title") or ""
        image_url = ""
        m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", content[:3000])
        if m:
            image_url = m.group(1)
        return {"ok": True, "error": None,
                "duration_ms": int((time.perf_counter()-start)*1000),
                "n_chars": len(content), "title": title,
                "published_at": "", "image_url": image_url}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "duration_ms": int((time.perf_counter()-start)*1000),
                "n_chars": 0, "title": "", "published_at": "", "image_url": ""}


async def process_url(spec):
    """串行: trafilatura -> zhipu_reader, 各自带 timeout, 不嵌 wait_for."""
    traf = await fetch_via_trafilatura(spec["url"])
    zr = await fetch_via_zhipu_reader(spec["url"])
    return {
        "url": spec["url"], "source": spec["source"], "query": spec["query"],
        "language": spec["language"], "section": spec["section"], "title": spec["title"],
        "domain": (spec["url"].split("/")[2] if "://" in spec["url"] else ""),
        "ref_chars": spec["ref_chars"],
        "ref_published_present": bool(spec["ref_published"]),
        "traf_ok": traf["ok"], "traf_error": traf.get("error"),
        "traf_duration_ms": traf["duration_ms"], "traf_n_chars": traf["n_chars"],
        "traf_title_present": bool(traf["title"]),
        "traf_published_present": bool(traf["published_at"]),
        "traf_image_present": bool(traf["image_url"]),
        "zr_ok": zr["ok"], "zr_error": zr.get("error"),
        "zr_duration_ms": zr["duration_ms"], "zr_n_chars": zr["n_chars"],
        "zr_title_present": bool(zr["title"]),
        "zr_image_present": bool(zr["image_url"]),
    }


async def main():
    urls = pick_urls()
    print(f"=== exp 2: 内容获取四方对比 ===")
    print(f"sample: {len(urls)} URLs")
    bocha_count = sum(1 for u in urls if u["source"] == "bocha")
    zhipu_count = sum(1 for u in urls if u["source"] == "zhipu_sogou")
    print(f"  from bocha: {bocha_count}, from zhipu_sogou: {zhipu_count}")
    print()
    if not urls:
        print("ERROR: no URLs")
        return

    rows = []
    for idx, spec in enumerate(urls, 1):
        row = await process_url(spec)
        rows.append(row)
        ts = "+" if row["traf_ok"] else "-"
        zs = "+" if row["zr_ok"] else "-"
        print(f"  [{idx:2d}/{len(urls)}] T{ts}Z{zs} src={row['source']:<12s} dom={row['domain'][:25]:<25s} "
              f"ref={row['ref_chars']:5d} traf={row['traf_n_chars']:6d}({row['traf_duration_ms']:5d}ms) "
              f"zr={row['zr_n_chars']:6d}({row['zr_duration_ms']:5d}ms)", flush=True)

        # 每 10 条 dump 一次中间结果防丢
        if idx % 10 == 0:
            (RESULTS_DIR / "exp2_raw.json").write_text(
                json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    out_raw = RESULTS_DIR / "exp2_raw.json"
    out_raw.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    out_csv = RESULTS_DIR / "exp2_summary.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"saved: {out_raw}")
    print(f"saved: {out_csv}")


if __name__ == "__main__":
    asyncio.run(main())
