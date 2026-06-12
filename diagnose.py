"""
端到端诊断脚本 — 分层穿透测试
从底层 API 到顶层 Agent，逐层验证，失败即定位。

用法: python diagnose.py [layer]
  layer: all | api | tools | agent | pipeline (默认 all)
"""
import asyncio
import sys
import time
import traceback
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


async def test_api_connectivity():
    """Layer 1: 测试所有外部 API 连通性"""
    print("\n" + "=" * 60)
    print("LAYER 1: API CONNECTIVITY")
    print("=" * 60)
    results = {}

    # 1.1 DeepSeek LLM
    print("\n[1.1] DeepSeek LLM API...")
    try:
        from app.services.llm_client import LLMClient
        llm = LLMClient()
        resp = await llm.simple_completion("You are a test.", "Say 'hello' in one word.", max_tokens=10)
        print(f"  ✅ DeepSeek OK: {resp[:50]}")
        results["deepseek"] = "OK"
    except Exception as e:
        print(f"  ❌ DeepSeek FAILED: {e}")
        results["deepseek"] = f"FAILED: {e}"

    # 1.2 Bocha Search
    print("\n[1.2] Bocha Search API...")
    try:
        from app.services.bocha_search import BochaSearchClient
        bocha = BochaSearchClient()
        health = bocha.health_snapshot()
        print(f"  Health: {health}")
        if health.get("health_state") == "disabled":
            print("  ⚠️  Bocha DISABLED (no API key or invalid)")
            results["bocha"] = "DISABLED"
        else:
            results_data = await bocha.search("polymer processing", count=3)
            print(f"  ✅ Bocha OK: {len(results_data)} results")
            results["bocha"] = f"OK ({len(results_data)} results)"
    except Exception as e:
        print(f"  ❌ Bocha FAILED: {e}")
        results["bocha"] = f"FAILED: {e}"

    # 1.3 Jina Reader
    print("\n[1.3] Jina Reader API...")
    try:
        from app.services.jina_reader import JinaReaderClient
        jina = JinaReaderClient()
        data = await jina.scrape("https://example.com", timeout_seconds=15)
        md = data.get("markdown", "")
        print(f"  ✅ Jina OK: {len(md)} chars")
        results["jina"] = f"OK ({len(md)} chars)"
    except Exception as e:
        print(f"  ❌ Jina FAILED: {e}")
        results["jina"] = f"FAILED: {e}"

    # 1.4 SiliconFlow Embedding
    print("\n[1.4] SiliconFlow Embedding API...")
    try:
        from app.services.semantic_dedup import SemanticDedup
        dedup = SemanticDedup()
        if dedup._api_enabled:
            vec = await dedup._encode(["polymer processing"])
            print(f"  ✅ SiliconFlow OK: dim={vec.shape[1]}")
            results["siliconflow"] = f"OK (dim={vec.shape[1]})"
        else:
            print("  ⚠️  SiliconFlow DISABLED (no API key)")
            results["siliconflow"] = "DISABLED"
    except Exception as e:
        print(f"  ❌ SiliconFlow FAILED: {e}")
        results["siliconflow"] = f"FAILED: {e}"

    return results


async def test_tools():
    """Layer 2: 测试每个 Agent 工具"""
    print("\n" + "=" * 60)
    print("LAYER 2: TOOL EXECUTION")
    print("=" * 60)
    results = {}

    from app.services.working_memory import WorkingMemory
    memory = WorkingMemory()

    # 2.1 WebSearchTool
    print("\n[2.1] WebSearchTool...")
    try:
        from app.services.bocha_search import BochaSearchClient
        from app.services.zhipu_search import ZhipuSearchClient
        from app.services.tools import WebSearchTool
        bocha = BochaSearchClient()
        zhipu = ZhipuSearchClient()
        tool = WebSearchTool(bocha_client=bocha, zhipu_client=zhipu)
        result = await tool.execute(memory=memory, query="polymer injection molding 2026")
        print(f"  {'✅' if result.success else '❌'} WebSearch: {result.summary[:80]}")
        results["web_search"] = "OK" if result.success else f"FAILED: {result.summary}"
    except Exception as e:
        print(f"  ❌ WebSearch EXCEPTION: {e}")
        results["web_search"] = f"EXCEPTION: {e}"

    # 2.2 ReadPageTool
    print("\n[2.2] ReadPageTool...")
    try:
        from app.services.scraper import ScraperClient
        from app.services.jina_reader import JinaReaderClient
        from app.services.tools import ReadPageTool
        jina = JinaReaderClient()
        scraper = ScraperClient(jina_client=jina)
        tool = ReadPageTool(scraper_client=scraper, timeout_seconds=20)
        # Use a real URL from search results if available
        test_url = "https://en.wikipedia.org/wiki/Polymer"
        if memory.search_results:
            test_url = memory.search_results[0].get("url", test_url)
        result = await tool.execute(memory=memory, url=test_url)
        print(f"  {'✅' if result.success else '❌'} ReadPage: {result.summary[:80]}")
        results["read_page"] = "OK" if result.success else f"FAILED: {result.summary}"
    except Exception as e:
        print(f"  ❌ ReadPage EXCEPTION: {e}")
        results["read_page"] = f"EXCEPTION: {e}"

    # 2.3 EvaluateArticleTool
    print("\n[2.3] EvaluateArticleTool...")
    try:
        from app.services.llm_client import LLMClient
        from app.services.tools import EvaluateArticleTool
        llm = LLMClient()
        tool = EvaluateArticleTool(llm_client=llm)
        # Use a real article from memory if available
        test_title = "New Breakthrough in Polymer Recycling Technology"
        test_content = "Researchers at MIT have developed a new method for recycling polymers..."
        if memory.page_read_meta:
            first_page = list(memory.page_read_meta.values())[0]
            test_title = first_page.get("title", test_title)
            test_content = first_page.get("markdown", test_content)[:3000]
        result = await tool.execute(
            memory=memory,
            title=test_title,
            content=test_content,
            url="https://example.com/test",
        )
        print(f"  {'✅' if result.success else '❌'} Evaluate: {result.summary[:80]}")
        results["evaluate"] = "OK" if result.success else f"FAILED: {result.summary}"
    except Exception as e:
        print(f"  ❌ Evaluate EXCEPTION: {e}")
        results["evaluate"] = f"EXCEPTION: {e}"

    # 2.4 SearchImagesTool
    print("\n[2.4] SearchImagesTool...")
    try:
        from app.services.tools import SearchImagesTool
        tool = SearchImagesTool(scraper_client=scraper)
        result = await tool.execute(memory=memory, topic="polymer processing")
        print(f"  {'✅' if result.success else '❌'} SearchImages: {result.summary[:80]}")
        results["search_images"] = "OK" if result.success else f"FAILED: {result.summary}"
    except Exception as e:
        print(f"  ❌ SearchImages EXCEPTION: {e}")
        results["search_images"] = f"EXCEPTION: {e}"

    # 2.5 WriteSectionTool
    print("\n[2.5] WriteSectionTool...")
    try:
        from app.services.tools import WriteSectionTool
        llm = LLMClient()
        tool = WriteSectionTool(llm_client=llm)
        # Add an industry article to memory if not already present
        from app.services.working_memory import ArticleSummary
        has_industry = any(a.section == "industry" for a in memory.discovered_articles)
        if not has_industry:
            memory.discovered_articles.append(ArticleSummary(
                url="https://example.com/test",
                title="Test Article about Polymer Injection Molding",
                domain="example.com",
                source_name="Example",
                section="industry",
                published_at="2026-06-10",
                summary="A new breakthrough in polymer injection molding technology was announced.",
                key_finding="Test finding about polymer recycling",
                image_url=None,
            ))
        result = await tool.execute(memory=memory, section="industry")
        print(f"  {'✅' if result.success else '❌'} WriteSection: {result.summary[:80]}")
        results["write_section"] = "OK" if result.success else f"FAILED: {result.summary}"
    except Exception as e:
        print(f"  ❌ WriteSection EXCEPTION: {e}")
        results["write_section"] = f"EXCEPTION: {e}"

    # 2.6 FinishTool
    print("\n[2.6] FinishTool...")
    try:
        from app.services.tools import FinishTool
        llm = LLMClient()
        tool = FinishTool(llm_client=llm)
        result = await tool.execute(
            memory=memory,
            summary="Test report summary",
            sections_written=["industry"],
            total_articles=1,
        )
        print(f"  {'✅' if result.success else '❌'} Finish: {result.summary[:80]}")
        results["finish"] = "OK" if result.success else f"FAILED: {result.summary}"
    except Exception as e:
        print(f"  ❌ Finish EXCEPTION: {e}")
        results["finish"] = f"EXCEPTION: {e}"

    return results


async def test_agent_loop():
    """Layer 3: 测试 AgentCore 循环（最小化任务）"""
    print("\n" + "=" * 60)
    print("LAYER 3: AGENT LOOP (minimal task)")
    print("=" * 60)
    results = {}

    try:
        from app.services.agent_core import AgentCore
        from app.services.harness import Harness
        from app.services.llm_client import LLMClient
        from app.services.bocha_search import BochaSearchClient
        from app.services.zhipu_search import ZhipuSearchClient
        from app.services.scraper import ScraperClient
        from app.services.jina_reader import JinaReaderClient
        from app.services.tools import (
            WebSearchTool, ReadPageTool, EvaluateArticleTool,
            WriteSectionTool, FinishTool, CheckCoverageTool,
        )

        llm = LLMClient()
        bocha = BochaSearchClient()
        zhipu = ZhipuSearchClient()
        jina = JinaReaderClient()
        scraper = ScraperClient(jina_client=jina)

        tools = [
            WebSearchTool(bocha_client=bocha, zhipu_client=zhipu),
            ReadPageTool(scraper_client=scraper, timeout_seconds=20),
            EvaluateArticleTool(llm_client=llm),
            WriteSectionTool(llm_client=llm),
            FinishTool(llm_client=llm),
            CheckCoverageTool(),
        ]

        harness = Harness(
            max_steps=10,
            max_duration_seconds=120,
        )

        agent = AgentCore(tools=tools, llm_client=llm, harness=harness)

        task = """你是一个测试 Agent。请执行以下步骤：
1. 用 web_search 搜索 "polymer recycling 2026"
2. 用 read_page 阅读第一个结果
3. 用 evaluate_article 评估该文章
4. 用 finish 完成任务，summary 写 "Agent loop test complete"

每次只调用一个工具。不要做多余的事情。"""

        print("\n[3.1] Running AgentCore with 10-step limit...")
        start = time.time()
        result = await agent.run(task=task)
        duration = time.time() - start

        print(f"  Finished reason: {result.finished_reason}")
        print(f"  Articles found: {len(result.articles)}")
        print(f"  Steps used: {result.step_count}")
        print(f"  Tokens used: {result.total_tokens}")
        print(f"  Duration: {duration:.1f}s")

        if result.finished_reason in ("finish", "finish_tool"):
            print("  [OK] Agent loop completed normally")
            results["agent_loop"] = "OK"
        else:
            print(f"  [WARN] Agent loop ended with: {result.finished_reason}")
            results["agent_loop"] = f"ENDED: {result.finished_reason}"

    except Exception as e:
        print(f"  [ERROR] Agent loop EXCEPTION: {e}")
        traceback.print_exc()
        results["agent_loop"] = f"EXCEPTION: {e}"

    return results


async def test_pipeline():
    """Layer 4: 测试完整 DailyReportAgent 流水线"""
    print("\n" + "=" * 60)
    print("LAYER 4: FULL PIPELINE (DailyReportAgent)")
    print("=" * 60)
    results = {}

    try:
        from app.services.daily_report_agent import DailyReportAgent
        from app.database import session_scope
        from app.services.repository import get_report_settings

        agent = DailyReportAgent()

        print("\n[4.1] Running DailyReportAgent (this may take 5-15 minutes)...")
        start = time.time()

        report = await agent.run(
            shadow_mode=False,
            mode="publish",
        )
        duration = time.time() - start

        print(f"  Report ID: {report.id}")
        print(f"  Status: {report.status}")
        print(f"  Items: {len(report.items) if hasattr(report, 'items') else 'N/A'}")
        print(f"  Duration: {duration:.1f}s")

        if report.status in ("complete", "partial"):
            print("  ✅ Pipeline completed")
            results["pipeline"] = f"OK (status={report.status}, {duration:.0f}s)"
        else:
            print(f"  ⚠️  Pipeline status: {report.status}")
            results["pipeline"] = f"STATUS: {report.status}"

    except Exception as e:
        print(f"  ❌ Pipeline EXCEPTION: {e}")
        traceback.print_exc()
        results["pipeline"] = f"EXCEPTION: {e}"

    return results


async def main():
    layer = sys.argv[1] if len(sys.argv) > 1 else "all"

    print(f"🔧 Polymer News Platform Diagnostic Tool")
    print(f"   Layer: {layer}")
    print(f"   Time: {datetime.now().isoformat()}")

    all_results = {}

    if layer in ("all", "api"):
        all_results["api"] = await test_api_connectivity()

    if layer in ("all", "tools"):
        all_results["tools"] = await test_tools()

    if layer in ("all", "agent"):
        all_results["agent"] = await test_agent_loop()

    if layer in ("all", "pipeline"):
        all_results["pipeline"] = await test_pipeline()

    # Summary
    print("\n" + "=" * 60)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 60)
    for layer_name, layer_results in all_results.items():
        print(f"\n{layer_name.upper()}:")
        for key, val in layer_results.items():
            status = "✅" if "OK" in str(val) else "❌" if "FAIL" in str(val) or "EXCEPTION" in str(val) else "⚠️"
            print(f"  {status} {key}: {val}")


if __name__ == "__main__":
    asyncio.run(main())
