"""分析 exp1 结果, 输出 5 个关键问题的答案."""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).parent
data = json.loads((ROOT / "results" / "exp1_raw.json").read_text(encoding="utf-8"))
rows = data["rows"]
print(f"total rows: {len(rows)}, all ok: {all(r['ok'] for r in rows)}")
print(f"balance_before: {data.get('balance_before')}, balance_after: {data.get('balance_after')}")
print()

print("=== Q1: count 边际收益 (summary=true, freshness=oneWeek, 6 queries 平均) ===")
print(f"  {'count':>5s} {'avg_n':>7s} {'avg_dom':>8s} {'avg_lat_ms':>11s}")
for c in [10, 30, 50]:
    sub = [r for r in rows if r["param_count"] == c and r["param_summary"] is True and r["param_freshness"] == "oneWeek"]
    avg_n = statistics.mean([r["n_results"] for r in sub])
    avg_dom = statistics.mean([r["uniq_domains"] for r in sub])
    avg_lat = statistics.mean([r["latency_ms"] for r in sub])
    print(f"  {c:5d} {avg_n:7.1f} {avg_dom:8.1f} {avg_lat:11.0f}")
print()

print("=== Q2: summary=true vs false (count=10, freshness=oneWeek) ===")
for s in [True, False]:
    sub = [r for r in rows if r["param_count"] == 10 and r["param_summary"] is s and r["param_freshness"] == "oneWeek"]
    avg_summary_p50 = statistics.mean([r["summary_chars_p50"] for r in sub])
    avg_snippet = statistics.mean([r["snippet_chars_avg"] for r in sub])
    avg_lat = statistics.mean([r["latency_ms"] for r in sub])
    print(f"  summary={s}: summary_p50={avg_summary_p50:.0f}, snippet_avg={avg_snippet:.0f}, lat={avg_lat:.0f}ms")
print()

print("=== Q3: freshness 召回曲线 (count=30, summary=true) ===")
print(f"  {'freshness':>10s} {'avg_n':>7s} {'avg_dom':>8s} {'pub_rate':>10s}")
for f in ["oneDay", "oneWeek", "oneMonth", "noLimit"]:
    sub = [r for r in rows if r["param_count"] == 30 and r["param_summary"] is True and r["param_freshness"] == f]
    avg_n = statistics.mean([r["n_results"] for r in sub])
    avg_dom = statistics.mean([r["uniq_domains"] for r in sub])
    pub_rate = statistics.mean([r["n_with_published"] / max(r["n_results"], 1) for r in sub])
    print(f"  {f:>10s} {avg_n:7.1f} {avg_dom:8.1f} {pub_rate:10.1%}")
print()

print("=== Q4: count=50 实际兑现率 (每个 query 在不同 freshness 下) ===")
queries = list(dict.fromkeys(r["query"] for r in rows))
print(f"  {'query':<25s} {'1Day':>5s} {'1Wk':>5s} {'1Mo':>5s} {'noLim':>6s}")
for q in queries:
    parts = []
    for f in ["oneDay", "oneWeek", "oneMonth", "noLimit"]:
        sub = [r for r in rows if r["query"] == q and r["param_count"] == 50 and r["param_summary"] is True and r["param_freshness"] == f]
        parts.append(sub[0]["n_results"] if sub else 0)
    print(f"  {q[:25]:<25s} {parts[0]:5d} {parts[1]:5d} {parts[2]:5d} {parts[3]:6d}")
print()

print("=== Q5: 中英文对比 (count=30, summary=true, oneWeek) ===")
for lang in ["zh", "en"]:
    sub = [r for r in rows if r["param_count"] == 30 and r["param_summary"] is True and r["param_freshness"] == "oneWeek" and r["language"] == lang]
    avg_n = statistics.mean([r["n_results"] for r in sub])
    avg_dom = statistics.mean([r["uniq_domains"] for r in sub])
    avg_sum = statistics.mean([r["summary_chars_p50"] for r in sub])
    print(f"  {lang}: n={avg_n:.1f}, domains={avg_dom:.1f}, summary_p50={avg_sum:.0f}")
print()

print("=== Q6: summary 字数全样本分布 (除 summary=false 外) ===")
all_sum_p50 = [r["summary_chars_p50"] for r in rows if r["param_summary"] is True and r["n_results"] > 0]
all_sum_max = [r["summary_chars_max"] for r in rows if r["param_summary"] is True and r["n_results"] > 0]
all_sum_min = [r["summary_chars_min"] for r in rows if r["param_summary"] is True and r["n_results"] > 0]
print(f"  p50 of summary_p50: {statistics.median(all_sum_p50):.0f}")
print(f"  p50 of summary_max: {statistics.median(all_sum_max):.0f}")
print(f"  p50 of summary_min: {statistics.median(all_sum_min):.0f}")
print(f"  样本数: {len(all_sum_p50)}")

# 截断率: summary 是否经常打满 ~800?
truncated = sum(1 for r in rows if r["param_summary"] is True and r["summary_chars_max"] >= 790)
total_with_summary = sum(1 for r in rows if r["param_summary"] is True and r["n_results"] > 0)
print(f"  截断率 (max>=790): {truncated}/{total_with_summary} = {truncated/total_with_summary:.1%}")
