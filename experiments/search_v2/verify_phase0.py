"""验证 Phase 0 + Phase 0.5 — 不依赖 main.py 整体导入."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# Test 1: ContinuousIngester.run_rss_only 存在
print("=== Test 1: ContinuousIngester ===")
from app.services.ingester import ContinuousIngester
ing = ContinuousIngester()
assert hasattr(ing, "run_rss_only"), "run_rss_only missing!"
assert hasattr(ing, "run"), "run missing!"
print(f"  run: {ing.run.__doc__.splitlines()[0] if ing.run.__doc__ else 'no doc'}")
print(f"  run_rss_only: {ing.run_rss_only.__doc__.splitlines()[0] if ing.run_rss_only.__doc__ else 'no doc'}")


# Test 2: _seeds_too_stale 函数行为
print()
print("=== Test 2: _seeds_too_stale ===")
from app.services.daily_report_agent import _seeds_too_stale
from datetime import datetime, timedelta, timezone

# Case 1: 空列表 -> True
assert _seeds_too_stale([]) is True
print("  empty list -> stale: OK")

# Case 2: 全部 fresh
fresh = [{"ingested_at": datetime.now(timezone.utc) - timedelta(hours=1)}]
assert _seeds_too_stale(fresh) is False
print("  fresh seeds -> not stale: OK")

# Case 3: 全部 stale
stale = [{"ingested_at": datetime.now(timezone.utc) - timedelta(hours=48)}]
assert _seeds_too_stale(stale, max_age_hours=24) is True
print("  48h-old seeds (24h cutoff) -> stale: OK")

# Case 4: 字符串格式
str_fresh = [{"ingested_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}]
assert _seeds_too_stale(str_fresh) is False
print("  string ingested_at -> parsed correctly: OK")

# Case 5: 字段缺失
no_field = [{"url": "x"}]
assert _seeds_too_stale(no_field) is True
print("  missing ingested_at -> stale: OK")

# Case 6: 混合（有 fresh 有 stale 有缺失） -> 只要有一个 fresh 就 not stale
mixed = [
    {"ingested_at": datetime.now(timezone.utc) - timedelta(hours=48)},  # stale
    {"ingested_at": datetime.now(timezone.utc) - timedelta(hours=1)},   # fresh
    {"url": "no field"},
]
assert _seeds_too_stale(mixed) is False, "mixed should NOT be stale (1 fresh)"
print("  mixed list (1 fresh) -> not stale: OK")


# Test 3: composer.gather_seeds 返回值含 ingested_at
print()
print("=== Test 3: composer.gather_seeds returns ingested_at ===")
from app.services.composer import DailyComposer
import inspect
src = inspect.getsource(DailyComposer.gather_seeds)
assert '"ingested_at": a.ingested_at' in src or "'ingested_at': a.ingested_at" in src, \
    "gather_seeds() does NOT include ingested_at in returned dict!"
print("  gather_seeds() includes ingested_at: OK")


# Test 4: bocha + zhipu health_snapshot 含新字段
print()
print("=== Test 4: health_snapshot new fields ===")
from app.services.bocha_search import BochaSearchClient
from app.services.zhipu_search import ZhipuSearchClient

new_fields = ["avg_results_per_query", "p50_latency_ms", "p95_latency_ms", "total_results"]
bocha = BochaSearchClient()
bocha_snap = bocha.health_snapshot()
for f in new_fields:
    assert f in bocha_snap, f"bocha missing {f}"
assert "p50_summary_chars" in bocha_snap
print(f"  Bocha health_snapshot has {len([k for k in bocha_snap if k in new_fields or 'summary_chars' in k])} new fields: OK")

zhipu = ZhipuSearchClient()
zhipu_snap = zhipu.health_snapshot()
for f in new_fields:
    assert f in zhipu_snap, f"zhipu missing {f}"
assert "p50_content_chars" in zhipu_snap
print(f"  Zhipu health_snapshot has new fields: OK")


print()
print("=" * 50)
print("ALL PHASE 0 + 0.5 STRUCTURAL TESTS PASSED")
print("=" * 50)
