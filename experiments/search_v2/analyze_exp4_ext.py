"""分析 exp4_ext 结果."""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).parent
data = json.loads((ROOT / "results" / "exp4_ext_raw.json").read_text(encoding="utf-8"))
rows = data["rows"]
print(f"total rows: {len(rows)}, all ok: {all(r['ok'] for r in rows)}")
print()

print("=== Q1: count 兑现率 (oneWeek 固定) ===")
print(f"  {'count':>5s} {'avg_n':>7s} {'avg_link':>9s} {'avg_lat_ms':>11s} {'content_p50':>12s}")
for c in [10, 30, 50]:
    sub = [r for r in rows if r["param_count"] == c and r["param_freshness"] == "oneWeek"]
    avg_n = statistics.mean([r["n_results"] for r in sub])
    avg_link = statistics.mean([r["n_with_link"] for r in sub])
    avg_lat = statistics.mean([r["latency_ms"] for r in sub])
    content_p50_avg = statistics.mean([r["content_p50"] for r in sub])
    print(f"  {c:5d} {avg_n:7.1f} {avg_link:9.1f} {avg_lat:11.0f} {content_p50_avg:12.0f}")
print()

print("=== Q2: freshness 召回 (count=30 固定) ===")
print(f"  {'freshness':>10s} {'avg_n':>7s} {'pub_rate':>10s}")
for f in ["oneDay", "oneWeek", "oneMonth", "noLimit"]:
    sub = [r for r in rows if r["param_count"] == 30 and r["param_freshness"] == f]
    avg_n = statistics.mean([r["n_results"] for r in sub])
    pub_rate = statistics.mean([r["n_with_publish"] / max(r["n_results"], 1) for r in sub])
    print(f"  {f:>10s} {avg_n:7.1f} {pub_rate:10.1%}")
print()

print("=== Q3: 与 Bocha URL 重叠率 (count=50, oneWeek) ===")
print(f"  {'query':<28s} {'zhipu_n':>8s} {'bocha_n':>8s} {'overlap':>8s}")
sub = [r for r in rows if r["param_count"] == 50 and r["param_freshness"] == "oneWeek"]
for r in sub:
    print(f"  {r['query'][:28]:<28s} {r['n_results']:8d} {r['bocha_url_count']:8d} {r['overlap_with_bocha']:8.1%}")
overall_overlap = statistics.mean([r["overlap_with_bocha"] for r in sub])
print(f"  --- average overlap: {overall_overlap:.1%} ---")
print()

print("=== Q4: 中英文召回与 content 字数对比 (count=30, oneWeek) ===")
for lang in ["zh", "en"]:
    sub = [r for r in rows if r["param_count"] == 30 and r["param_freshness"] == "oneWeek" and r["language"] == lang]
    avg_n = statistics.mean([r["n_results"] for r in sub])
    avg_content = statistics.mean([r["content_avg"] for r in sub])
    avg_lat = statistics.mean([r["latency_ms"] for r in sub])
    print(f"  {lang}: n={avg_n:.1f}, content_avg={avg_content:.0f}, lat={avg_lat:.0f}ms")
print()

print("=== Q5: count 与延迟对比 (oneWeek 固定, 看 count 变大延迟变化) ===")
for c in [10, 30, 50]:
    sub = [r for r in rows if r["param_count"] == c and r["param_freshness"] == "oneWeek"]
    avg_lat = statistics.mean([r["latency_ms"] for r in sub])
    p95 = sorted([r["latency_ms"] for r in sub])[int(len(sub)*0.95)] if sub else 0
    avg_content_total = statistics.mean([r["content_avg"] * r["n_results"] for r in sub])
    print(f"  count={c}: avg_lat={avg_lat:.0f}ms, p95={p95}ms, total_content_chars/query={avg_content_total:.0f}")
print()

# Bocha vs Zhipu 单调用产出量对比 (count=50)
print("=== Q6: Bocha vs Zhipu Sogou 单次调用「内容产出」对比 ===")
print("(Bocha summary=800字截断 vs Zhipu sogou content=完整)")
sub_zhipu_50 = [r for r in rows if r["param_count"] == 50 and r["param_freshness"] == "oneWeek"]
zhipu_total_chars = statistics.mean([r["content_avg"] * r["n_results"] for r in sub_zhipu_50])
print(f"  Zhipu sogou count=50: ~{zhipu_total_chars:.0f} 字/次调用 (即 ~{zhipu_total_chars/1000:.1f}K 字)")
print(f"  Bocha count=50 summary=true: ~{800*38:.0f} 字/次调用 (50条x800字截断)")
print(f"  Zhipu 信息密度倍数: {zhipu_total_chars / (800*38):.1f}x")
