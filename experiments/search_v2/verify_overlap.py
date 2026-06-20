"""验证 0% 重叠率: 抽几个 URL 看 Bocha 和智谱实际拿到了什么."""
import json
from pathlib import Path

ROOT = Path("experiments/search_v2/results")
exp1 = json.loads((ROOT / "exp1_raw.json").read_text(encoding="utf-8"))
exp4 = json.loads((ROOT / "exp4_ext_raw.json").read_text(encoding="utf-8"))

# 取 query=注塑机 新品发布 的 count=50 oneWeek 数据
target = "注塑机 新品发布"

# Bocha
bocha_row = next(r for r in exp1["rows"]
                 if r["query"] == target and r["param_count"] == 50
                 and r["param_summary"] is True and r["param_freshness"] == "oneWeek")
bocha_urls = [item.get("url", "") for item in (bocha_row.get("raw_results") or [])]

# Zhipu sogou
zhipu_row = next(r for r in exp4["rows"]
                 if r["query"] == target and r["param_count"] == 50
                 and r["param_freshness"] == "oneWeek")
zhipu_urls = [r.get("link", "") for r in (zhipu_row.get("raw_results") or [])]

print(f"=== Query: {target} ===")
print(f"Bocha 拿到 {len(bocha_urls)} 条 URL")
print(f"  前 5 条 (域名):")
for u in bocha_urls[:5]:
    if "://" in u:
        dom = u.split("/")[2]
        print(f"    {dom}  -- {u[:100]}")
print()
print(f"Zhipu sogou 拿到 {len(zhipu_urls)} 条 URL")
print(f"  前 5 条 (域名):")
for u in zhipu_urls[:5]:
    if "://" in u:
        dom = u.split("/")[2]
        print(f"    {dom}  -- {u[:100]}")
print()

# 计算域名级重叠 (有些 URL 不同但域名相同, 算"信源重叠")
def domains(urls):
    return {u.split("/")[2] for u in urls if "://" in u}

bd = domains(bocha_urls)
zd = domains(zhipu_urls)
print(f"Bocha 唯一域名: {len(bd)}")
print(f"Zhipu 唯一域名: {len(zd)}")
print(f"域名交集: {len(bd & zd)} ({(len(bd & zd) / max(len(bd | zd), 1) * 100):.0f}% Jaccard)")
print(f"域名交集示例: {list((bd & zd))[:10]}")
print()

# URL 标准化: 去掉 protocol/trailing slash/queryparam 看是否还是 0
import re
def normalize(url):
    # remove protocol, www, trailing /, common tracking params
    u = re.sub(r"^https?://(www\.)?", "", url.strip())
    u = u.rstrip("/")
    # 去掉 query 部分用于"是否同一篇文章"判断
    u_no_query = u.split("?")[0]
    return u_no_query

bocha_norm = {normalize(u) for u in bocha_urls}
zhipu_norm = {normalize(u) for u in zhipu_urls}
print(f"标准化后 URL 交集: {len(bocha_norm & zhipu_norm)}")
if (bocha_norm & zhipu_norm):
    print(f"  样本: {list(bocha_norm & zhipu_norm)[:5]}")
