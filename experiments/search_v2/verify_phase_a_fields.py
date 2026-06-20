"""V2 Phase A 验证脚本：检查 bocha / zhipu 返回结果是否含 summary_full + snippet_raw 字段。"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("verify_phase_a")

# ── 项目根目录加入 sys.path ──
from pathlib import Path
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


async def verify_bocha():
    from app.services.bocha_search import BochaSearchClient
    client = BochaSearchClient()
    if not client.enabled:
        logger.warning("BochaSearchClient disabled (no API key), skipping live test")
        return

    results = await client.search("polymer recycling", count=3)
    if not results:
        logger.warning("Bocha search returned 0 results")
        return

    r0 = results[0]
    has_sf = "summary_full" in r0
    has_sr = "snippet_raw" in r0
    logger.info(
        "[bocha] results=%d  summary_full=%s(%d chars)  snippet_raw=%s(%d chars)  snippet=%s(%d chars)",
        len(results),
        "OK" if has_sf else "MISSING",
        len(r0.get("summary_full") or ""),
        "OK" if has_sr else "MISSING",
        len(r0.get("snippet_raw") or ""),
        "OK" if "snippet" in r0 else "MISSING",
        len(r0.get("snippet") or ""),
    )
    assert has_sf, "bocha result missing 'summary_full'"
    assert has_sr, "bocha result missing 'snippet_raw'"
    logger.info("[bocha] PASS — both new fields present")


async def verify_zhipu():
    from app.services.zhipu_search import ZhipuSearchClient
    client = ZhipuSearchClient()
    if not client.enabled:
        logger.warning("ZhipuSearchClient disabled (no API key), skipping live test")
        return

    results = await client.search("高分子材料回收", count=3)
    if not results:
        logger.warning("Zhipu search returned 0 results")
        return

    r0 = results[0]
    has_sf = "summary_full" in r0
    has_sr = "snippet_raw" in r0
    sf_len = len(r0.get("summary_full") or "")
    sr_len = len(r0.get("snippet_raw") or "")
    snippet_len = len(r0.get("snippet") or "")
    logger.info(
        "[zhipu] results=%d  summary_full=%s(%d chars)  snippet_raw=%s(%d chars)  snippet=%s(%d chars)",
        len(results),
        "OK" if has_sf else "MISSING",
        sf_len,
        "OK" if has_sr else "MISSING",
        sr_len,
        "OK" if "snippet" in r0 else "MISSING",
        snippet_len,
    )
    assert has_sf, "zhipu result missing 'summary_full'"
    assert has_sr, "zhipu result missing 'snippet_raw'"
    # content_size=high 模式下中文 content 通常 >= 1000 字符
    if sf_len >= 1000:
        logger.info("[zhipu] summary_full length %d >= 1000 (content_size=high verified)", sf_len)
    else:
        logger.warning("[zhipu] summary_full length %d < 1000 (may not be content_size=high)", sf_len)
    logger.info("[zhipu] PASS — both new fields present")


async def verify_zhipu_engine_warning():
    """A.5: 验证 engine 防御性 warning 逻辑。"""
    from app.services import zhipu_search
    from app.config import settings

    original_engine = settings.zhipu_search_engine
    original_warned = zhipu_search.ZhipuSearchClient._engine_warned

    try:
        # frozen dataclass 需要 object.__setattr__ 来临时修改
        object.__setattr__(settings, "zhipu_search_engine", "search_std")
        zhipu_search.ZhipuSearchClient._engine_warned = False

        # 构造 client 应该触发 warning
        client = zhipu_search.ZhipuSearchClient(api_key="dummy")
        logger.info("[zhipu A.5] Created client with search_std — check logger output above for WARNING")
        assert zhipu_search.ZhipuSearchClient._engine_warned is True, "Expected _engine_warned=True"
        logger.info("[zhipu A.5] PASS — engine warning triggered once")
    finally:
        object.__setattr__(settings, "zhipu_search_engine", original_engine)
        zhipu_search.ZhipuSearchClient._engine_warned = original_warned


async def main():
    logger.info("=== V2 Phase A Field Verification ===")

    logger.info("--- A.2: bocha summary_full / snippet_raw ---")
    await verify_bocha()

    logger.info("--- A.4: zhipu summary_full / snippet_raw ---")
    await verify_zhipu()

    logger.info("--- A.5: zhipu engine warning ---")
    await verify_zhipu_engine_warning()

    logger.info("=== ALL CHECKS DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
