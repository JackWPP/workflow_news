"""分析 exp2: 内容获取四方对比."""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).parent
data = json.loads((ROOT / "results" / "exp2_raw.json").read_text(encoding="utf-8"))
rows = data["rows"]
print(f"total: {len(rows)}")

# Q1: 抓取成功率
print()
print("=== Q1: 抓取成功率 (按来源拆分) ===")
for src in ["bocha", "zhipu_sogou"]:
    sub = [r for r in rows if r["source"] == src]
    traf_ok = sum(1 for r in sub if r["traf_ok"])
    zr_ok = sum(1 for r in sub if r["zr_ok"])
    either_ok = sum(1 for r in sub if r["traf_ok"] or r["zr_ok"])
    both_ok = sum(1 for r in sub if r["traf_ok"] and r["zr_ok"])
    print(f"  {src:<12s} (n={len(sub)}): traf={traf_ok}/{len(sub)} ({traf_ok/len(sub):.0%}), "
          f"zr={zr_ok}/{len(sub)} ({zr_ok/len(sub):.0%}), "
          f"either={either_ok}/{len(sub)} ({either_ok/len(sub):.0%}), "
          f"both={both_ok}/{len(sub)} ({both_ok/len(sub):.0%})")

# Q2: 字数对比 (4 路)
print()
print("=== Q2: 字数对比 (仅成功抓取的样本) ===")

def stat(vals):
    if not vals:
        return "n=0"
    s = sorted(vals)
    return f"n={len(vals)}, p50={s[len(s)//2]}, p95={s[int(len(s)*0.95)]}, max={s[-1]}, avg={int(statistics.mean(vals))}"

# Reference (Bocha summary 800 / 智谱 content high)
bocha_ref = [r["ref_chars"] for r in rows if r["source"] == "bocha"]
zhipu_ref = [r["ref_chars"] for r in rows if r["source"] == "zhipu_sogou"]
print(f"  Bocha summary  (ref):  {stat(bocha_ref)}")
print(f"  Zhipu content  (ref):  {stat(zhipu_ref)}")
# Trafilatura
traf_chars = [r["traf_n_chars"] for r in rows if r["traf_ok"]]
print(f"  Trafilatura full:      {stat(traf_chars)}")
# Zhipu reader
zr_chars = [r["zr_n_chars"] for r in rows if r["zr_ok"]]
print(f"  Zhipu reader full:     {stat(zr_chars)}")

# Q3: 延迟对比
print()
print("=== Q3: 延迟对比 (ms) ===")
traf_dur = [r["traf_duration_ms"] for r in rows if r["traf_ok"]]
zr_dur = [r["zr_duration_ms"] for r in rows if r["zr_ok"]]
print(f"  Trafilatura: {stat(traf_dur)}")
print(f"  Zhipu reader: {stat(zr_dur)}")

# Q4: 字段命中率
print()
print("=== Q4: 元数据字段命中率 ===")
for src in ["bocha", "zhipu_sogou"]:
    sub = [r for r in rows if r["source"] == src]
    print(f"  {src}:")
    ref_pub = sum(1 for r in sub if r["ref_published_present"])
    print(f"    搜索 API 自带 published: {ref_pub}/{len(sub)} ({ref_pub/len(sub):.0%})")
    traf_pub = sum(1 for r in sub if r["traf_ok"] and r["traf_published_present"])
    traf_ok_n = sum(1 for r in sub if r["traf_ok"])
    print(f"    Trafilatura published:    {traf_pub}/{traf_ok_n} ({traf_pub/max(traf_ok_n,1):.0%})")
    traf_img = sum(1 for r in sub if r["traf_ok"] and r["traf_image_present"])
    print(f"    Trafilatura image:        {traf_img}/{traf_ok_n} ({traf_img/max(traf_ok_n,1):.0%})")
    zr_img = sum(1 for r in sub if r["zr_ok"] and r["zr_image_present"])
    zr_ok_n = sum(1 for r in sub if r["zr_ok"])
    print(f"    Zhipu reader image:       {zr_img}/{zr_ok_n} ({zr_img/max(zr_ok_n,1):.0%})")

# Q5: 智谱 URL 真文章率 (vs 营销页/落地页)
print()
print("=== Q5: 智谱 URL 真文章率 (启发式: trafilatura+智谱 reader 任一抽出>500字 = 真文章) ===")
zhipu_rows = [r for r in rows if r["source"] == "zhipu_sogou"]
real_article = sum(1 for r in zhipu_rows if max(r["traf_n_chars"], r["zr_n_chars"]) >= 500)
print(f"  zhipu_sogou: {real_article}/{len(zhipu_rows)} ({real_article/len(zhipu_rows):.0%}) 拿到 >=500 字内容")
short_only = sum(1 for r in zhipu_rows if 0 < max(r["traf_n_chars"], r["zr_n_chars"]) < 500)
print(f"  zhipu_sogou: {short_only}/{len(zhipu_rows)} ({short_only/len(zhipu_rows):.0%}) 只拿到 <500 字 (可能是首页/营销页)")
zero = sum(1 for r in zhipu_rows if r["traf_n_chars"] == 0 and r["zr_n_chars"] == 0)
print(f"  zhipu_sogou: {zero}/{len(zhipu_rows)} ({zero/len(zhipu_rows):.0%}) 完全失败")

# Bocha 同样标准
print()
bocha_rows = [r for r in rows if r["source"] == "bocha"]
real_article = sum(1 for r in bocha_rows if max(r["traf_n_chars"], r["zr_n_chars"]) >= 500)
print(f"  bocha:       {real_article}/{len(bocha_rows)} ({real_article/len(bocha_rows):.0%}) 拿到 >=500 字内容")
short_only = sum(1 for r in bocha_rows if 0 < max(r["traf_n_chars"], r["zr_n_chars"]) < 500)
print(f"  bocha:       {short_only}/{len(bocha_rows)} ({short_only/len(bocha_rows):.0%}) 只拿到 <500 字")
zero = sum(1 for r in bocha_rows if r["traf_n_chars"] == 0 and r["zr_n_chars"] == 0)
print(f"  bocha:       {zero}/{len(bocha_rows)} ({zero/len(bocha_rows):.0%}) 完全失败")

# Q6: Bocha summary 截断 vs Trafilatura 全文 (是否值得抓?)
print()
print("=== Q6: Bocha summary 是否够用? (vs 全文) ===")
# 取 Bocha 来源 + traf 成功 的样本, 看 ref_chars vs traf_n_chars
sub = [r for r in rows if r["source"] == "bocha" and r["traf_ok"] and r["traf_n_chars"] > 0]
print(f"  样本: {len(sub)} 条 Bocha URL, trafilatura 抓取成功")
ref_avg = statistics.mean([r["ref_chars"] for r in sub])
traf_avg = statistics.mean([r["traf_n_chars"] for r in sub])
print(f"  Bocha summary avg: {ref_avg:.0f} 字")
print(f"  全文 avg:          {traf_avg:.0f} 字")
print(f"  比例 (全文/summary): {traf_avg/ref_avg:.1f}x")

# 看每条的差距分布
ratios = [(r["traf_n_chars"] / max(r["ref_chars"], 1)) for r in sub]
print(f"  各样本全文/summary 比: p50={sorted(ratios)[len(ratios)//2]:.1f}x, p95={sorted(ratios)[int(len(ratios)*0.95)]:.1f}x, max={max(ratios):.1f}x")

# 极端: 多少 % 的样本, 全文比 summary 大 3 倍以上?
big_diff = sum(1 for r in ratios if r > 3)
print(f"  全文 > 3× summary 的样本: {big_diff}/{len(ratios)} ({big_diff/len(ratios):.0%})")

# Q7: Zhipu reader 字数 vs Zhipu content (同来源对比)
print()
print("=== Q7: 智谱 sogou content vs 智谱 reader 抽全文 (同 URL 对比) ===")
sub = [r for r in rows if r["source"] == "zhipu_sogou" and r["zr_ok"] and r["zr_n_chars"] > 0]
print(f"  样本: {len(sub)} 条, zhipu reader 成功")
ref_avg = statistics.mean([r["ref_chars"] for r in sub])
zr_avg = statistics.mean([r["zr_n_chars"] for r in sub])
print(f"  搜索 content avg: {ref_avg:.0f} 字")
print(f"  reader 全文 avg:  {zr_avg:.0f} 字")
print(f"  比例:             {zr_avg/ref_avg:.1f}x")
ratios = [(r["zr_n_chars"] / max(r["ref_chars"], 1)) for r in sub]
print(f"  p50={sorted(ratios)[len(ratios)//2]:.1f}x, max={max(ratios):.1f}x")
