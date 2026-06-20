# 搜索与页面读取改进方案（工程师执行版）

> **日期**: 2026-06-19
> **前提**: 已完成 4 轮实验验证，数据见 `experiments/search_comparison/`
> **原则**: 每个任务必须有验证步骤，验证不通过不进入下一步

---

## 〇、现有依赖注入图（改动前基线）

```
DailyReportAgent._run_phases() (daily_report_agent.py:247)
  ├── jina = JinaReaderClient()                              # line 257
  ├── scraper = ScraperClient(jina_client=jina)              # line 258
  ├── bocha = BochaSearchClient()                            # line 259
  ├── zhipu = ZhipuSearchClient()                            # line 320
  └── agent_tools = [
        WebSearchTool(bocha_client=bocha, zhipu_client=zhipu),  # line 322
        ReadPageTool(scraper_client=scraper, timeout_seconds=runtime["scrape_timeout_seconds"]),  # line 323
        ...
      ]

ScoutAgent._build_tools() (scout_agent.py:76)
  ├── bocha = BochaSearchClient()                            # line 77
  ├── scraper = ScraperClient()                              # line 78, 自动创建 Jina
  └── tools = [
        WebSearchTool(bocha_client=bocha),                    # line 81, 无 router
        BochaAiSearchTool(bocha_client=bocha),                # line 82
        ReadPageTool(scraper_client=scraper),                 # line 83
        ...
      ]

ExplorerAgent._build_tools() (explorer_agent.py:103)
  ├── bocha = BochaSearchClient()                            # line 104
  ├── scraper = ScraperClient()                              # line 105
  ├── router = SearchRouter(bocha_client=bocha)              # line 107
  └── tools = [
        WebSearchTool(bocha_client=bocha, search_router=router),  # line 109, 有 router
        ReadPageTool(scraper_client=scraper),                 # line 110
        ...
      ]

ContinuousIngester.search_engine (ingester.py:106)
  ├── bocha_client = BochaSearchClient()                     # line 113
  ├── router = SearchRouter(bocha_client=bocha_client)       # line 115
  └── SearchEngine(bocha_client=bocha_client, search_router=router)  # line 114
```

**关键观察**：
- `WebSearchTool` 有两种注入模式：带 `search_router`（ExplorerAgent）和不带（DailyReportAgent, ScoutAgent）
- `SearchRouter` 目前只接受 `bocha_client` 和 `zhipu_client`
- `ScraperClient` 的 `jina_client` 可选，为 None 时自动创建

---

## 一、Phase 1：基础设施（不改任何现有代码行为）

### 1.1 CircuitBreaker

**新建文件**: `app/services/circuit_breaker.py`

**接口**:
```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 300):
        ...

    @property
    def state(self) -> str:  # "closed" | "open" | "half_open"
        ...

    def should_skip(self) -> bool:
        """state 为 open 且未到恢复时间 → 返回 True"""

    def record_success(self) -> None:
        """failure_count 归零，state → closed"""

    def record_failure(self) -> None:
        """failure_count += 1，达到阈值 → state = open，记录 last_failure_time"""
```

**验证步骤**:
```bash
python -c "
from app.services.circuit_breaker import CircuitBreaker
cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
assert cb.state == 'closed'
assert cb.should_skip() == False

# 连续失败 3 次 → open
cb.record_failure()
cb.record_failure()
cb.record_failure()
assert cb.state == 'open'
assert cb.should_skip() == True

# 恢复时间后 → half_open
import time; time.sleep(1.1)
assert cb.should_skip() == False
assert cb.state == 'half_open'

# 成功 → closed
cb.record_success()
assert cb.state == 'closed'
print('CircuitBreaker: ALL TESTS PASSED')
"
```

**回滚条件**: 测试不通过 → 不进入下一步。

---

### 1.2 FreeSearchProvider

**新建文件**: `app/services/free_search_provider.py`

**接口**:
```python
class FreeSearchProvider:
    """DuckDuckGo + Brave 并行搜索，带熔断器"""

    def __init__(self):
        self._ddg_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=300)
        self._brave_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=300)

    @property
    def enabled(self) -> bool:
        """至少一个引擎的熔断器未 open"""

    async def search(self, query: str, count: int = 10) -> list[dict[str, Any]]:
        """
        并行请求 DDG + Brave，合并去重返回。
        结果格式与 BochaSearchClient.search() 一致：
        {url, title, snippet, domain, provider: "ddg"|"brave", ...}
        """

    async def search_ddg(self, query: str, count: int = 10) -> list[dict]:
        """单引擎搜索，内部调用熔断器"""

    async def search_brave(self, query: str, count: int = 10) -> list[dict]:
        """单引擎搜索，内部调用熔断器"""
```

**实现要点**（来自实验代码 `experiments/search_comparison/final_comparison.py`）:
- DDG: `POST https://html.duckduckgo.com/html/` + `data={"q": query, "b": ""}`
- Brave: `GET https://search.brave.com/search?q={query}` + `Accept-Encoding: identity`
- 超时: 15s（比 Bocha 的 12s 略长，因为是 HTML 抓取）
- User-Agent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36`

**验证步骤**:
```bash
python -c "
import asyncio
from app.services.free_search_provider import FreeSearchProvider

async def test():
    p = FreeSearchProvider()
    assert p.enabled == True

    # 测试中文查询
    results = await p.search('注塑机 新品发布', count=5)
    assert len(results) > 0, 'DDG+Brave should return results'
    assert all(r.get('url') for r in results), 'All results must have URL'
    assert all(r.get('title') for r in results), 'All results must have title'
    print(f'Chinese query: {len(results)} results')

    # 测试英文查询
    results = await p.search('polymer industry news', count=5)
    assert len(results) > 0
    print(f'English query: {len(results)} results')

    # 测试结果格式一致性
    r = results[0]
    assert 'url' in r and 'title' in r and 'snippet' in r and 'domain' in r
    print(f'Result format: {list(r.keys())}')

    print('FreeSearchProvider: ALL TESTS PASSED')

asyncio.run(test())
"
```

**回滚条件**: DDG 和 Brave 都无法返回结果 → 不进入下一步。

---

### 1.3 AnySearchProvider

**新建文件**: `app/services/anysearch_provider.py`

**接口**:
```python
class AnySearchProvider:
    """AnySearch API，英文高质量源"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ANYSEARCH_API_KEY", "")
        self._circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=300)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and not self._circuit_breaker.should_skip()

    async def search(self, query: str, count: int = 10) -> list[dict[str, Any]]:
        """
        POST https://api.anysearch.com/v1/search
        结果格式与 BochaSearchClient.search() 一致。
        """
```

**验证步骤**:
```bash
python -c "
import asyncio
from app.services.anysearch_provider import AnySearchProvider

async def test():
    p = AnySearchProvider()
    assert p.enabled == True

    results = await p.search('polymer industry news', count=5)
    assert len(results) > 0, 'AnySearch should return results'
    print(f'AnySearch: {len(results)} results')
    for r in results[:3]:
        print(f'  - {r[\"title\"][:50]}')

    print('AnySearchProvider: ALL TESTS PASSED')

asyncio.run(test())
"
```

**回滚条件**: API key 无效或 API 不可达 → 标记为 disabled，不阻塞其他步骤。

---

### 1.4 JSON-LD 提取

**修改文件**: `app/services/scraper.py`

**改动位置**: `_trafilatura_scrape()` 方法（line 170）和 `_fallback_scrape()` 方法（line 215）

**新增函数**:
```python
def _extract_json_ld(html: str) -> dict | None:
    """
    从 HTML 中提取 JSON-LD 结构化数据。
    优先级：@type=NewsArticle > @type=Article > 其他

    返回:
        {
            "title": str | None,
            "published_at": str | None,  # ISO 格式
            "image_url": str | None,
            "author": str | None,
            "description": str | None,
        }
        或 None（无 JSON-LD）
    """
```

**插入位置**: 在 `_extract_html_meta()` 调用之后，作为更高优先级的元数据来源。

**验证步骤**:
```bash
python -c "
from app.services.scraper import _extract_json_ld

# 测试有 JSON-LD 的 HTML
html = '''
<html><head>
<script type=\"application/ld+json\">
{\"@type\": \"NewsArticle\", \"headline\": \"Test Title\", \"datePublished\": \"2026-06-19\", \"image\": \"https://example.com/img.jpg\"}
</script>
</head><body></body></html>
'''
result = _extract_json_ld(html)
assert result is not None
assert result['title'] == 'Test Title'
assert result['published_at'] == '2026-06-19'
assert result['image_url'] == 'https://example.com/img.jpg'
print(f'JSON-LD extraction: {result}')

# 测试无 JSON-LD 的 HTML
html2 = '<html><body>No JSON-LD</body></html>'
result2 = _extract_json_ld(html2)
assert result2 is None
print('No JSON-LD: correctly returned None')

print('JSON-LD extraction: ALL TESTS PASSED')
"
```

**回滚条件**: 提取逻辑误解析非 JSON-LD 内容 → 检查正则表达式。

---

## 二、Phase 2：搜索架构改造

### 2.1 SearchRouter 扩展

**修改文件**: `app/services/search_router.py`

**改动点**:
1. 构造函数新增 `free_provider` 和 `anysearch_provider` 参数
2. `search()` 方法增加并行搜索逻辑
3. 保持向后兼容：不传新参数时行为不变

**改动前**（line 67）:
```python
def __init__(self, bocha_client=None, zhipu_client=None):
    self._bocha = bocha_client
    self._zhipu = zhipu_client
```

**改动后**:
```python
def __init__(
    self,
    bocha_client=None,
    zhipu_client=None,      # deprecated, 保留兼容
    free_provider=None,      # 新增：FreeSearchProvider
    anysearch_provider=None, # 新增：AnySearchProvider
):
    self._bocha = bocha_client
    self._zhipu = zhipu_client
    self._free = free_provider
    self._anysearch = anysearch_provider
```

**search() 方法改动**:
```python
async def search(self, query, language="zh", max_results=10, freshness="oneWeek", ...):
    # 1. 缓存检查（不变）

    # 2. 并行搜索（新增）
    import asyncio
    tasks = []

    # Bocha（始终尝试，支持 freshness/include/exclude）
    if self._bocha and self._bocha.enabled:
        tasks.append(("bocha", self._bocha.search(query, count=max_results, freshness=freshness, ...)))

    # 免费引擎（新增）
    if self._free and self._free.enabled:
        tasks.append(("free", self._free.search(query, count=max_results)))

    # AnySearch（仅英文，新增）
    if language == "en" and self._anysearch and self._anysearch.enabled:
        tasks.append(("anysearch", self._anysearch.search(query, count=max_results)))

    # 3. 并行执行，收集成功结果
    results_map = {}
    for name, coro in tasks:
        try:
            r = await asyncio.wait_for(coro, timeout=20)
            if r:
                results_map[name] = r
        except Exception:
            pass

    # 4. 合并：Bocha 优先，其他补充
    merged = results_map.get("bocha", [])
    seen_urls = {r["url"] for r in merged}
    for name in ["free", "anysearch"]:
        for r in results_map.get(name, []):
            if r["url"] not in seen_urls:
                merged.append(r)
                seen_urls.add(r["url"])

    # 5. blocklist 过滤 + 缓存（不变）
    ...
```

**验证步骤**:
```bash
# 验证 1: 不传新参数时行为不变
python -c "
import asyncio
from app.services.search_router import SearchRouter
from app.services.bocha_search import BochaSearchClient

async def test():
    bocha = BochaSearchClient()
    router = SearchRouter(bocha_client=bocha)  # 旧用法
    results = await router.search('polymer', language='en', max_results=3)
    print(f'Legacy mode: {len(results)} results')
    assert len(results) > 0 or not bocha.enabled  # 要么有结果，要么 Bocha 未配置

asyncio.run(test())
"

# 验证 2: 传入新参数时并行搜索
python -c "
import asyncio
from app.services.search_router import SearchRouter
from app.services.bocha_search import BochaSearchClient
from app.services.free_search_provider import FreeSearchProvider

async def test():
    bocha = BochaSearchClient()
    free = FreeSearchProvider()
    router = SearchRouter(bocha_client=bocha, free_provider=free)
    results = await router.search('polymer industry news', language='en', max_results=10)
    print(f'New mode: {len(results)} results')
    # 应该比单独 Bocha 或单独 free 多
    providers = set(r.get('provider', 'unknown') for r in results)
    print(f'Providers: {providers}')

asyncio.run(test())
"
```

**回滚条件**:
- 旧用法（不传新参数）返回结果数减少 → 回滚
- 新用法返回结果数 < 旧用法 → 不启用并行，退回旧逻辑

---

### 2.2 Ingester 改造

**修改文件**: `app/services/ingester.py`

**改动位置**: `search_engine` property（line 106）

**改动前**:
```python
@property
def search_engine(self):
    if self._search_engine is None:
        from app.services.bocha_search import BochaSearchClient
        from app.services.search_engine import SearchEngine
        from app.services.search_router import SearchRouter
        bocha_client = BochaSearchClient()
        self._search_engine = SearchEngine(
            bocha_client=bocha_client,
            search_router=SearchRouter(bocha_client=bocha_client),
        )
    return self._search_engine
```

**改动后**:
```python
@property
def search_engine(self):
    if self._search_engine is None:
        from app.services.bocha_search import BochaSearchClient
        from app.services.free_search_provider import FreeSearchProvider
        from app.services.search_engine import SearchEngine
        from app.services.search_router import SearchRouter
        bocha_client = BochaSearchClient()
        free_provider = FreeSearchProvider()
        router = SearchRouter(
            bocha_client=bocha_client,
            free_provider=free_provider,
        )
        self._search_engine = SearchEngine(
            bocha_client=bocha_client,
            search_router=router,
        )
    return self._search_engine
```

**验证步骤**:
```bash
# 验证: 手动触发一次 ingester，检查日志
python -c "
import asyncio
from app.services.ingester import ContinuousIngester

async def test():
    ingester = ContinuousIngester()
    # 只运行模板搜索，不写入数据库
    specs = ingester._build_search_query_specs()
    print(f'Search templates: {len(specs)}')

    # 测试单个查询
    engine = ingester.search_engine
    results = await engine.search('注塑机 新品发布', language='zh', max_results=3)
    print(f'Ingester search: {len(results)} results')
    for r in results[:2]:
        print(f'  - {r.get(\"title\",\"\")[:50]} from {r.get(\"provider\",\"?\")}')

asyncio.run(test())
"
```

**回滚条件**: Ingester 搜索返回 0 结果 → 回滚到旧代码。

---

### 2.3 DailyReportAgent 改造

**修改文件**: `app/services/daily_report_agent.py`

**改动位置**: `_run_phases()` 方法（line 247）

**改动前**（line 320-322）:
```python
zhipu = ZhipuSearchClient()
agent_tools = [
    WebSearchTool(bocha_client=bocha, zhipu_client=zhipu),
    ...
]
```

**改动后**:
```python
from app.services.free_search_provider import FreeSearchProvider
from app.services.anysearch_provider import AnySearchProvider
from app.services.search_router import SearchRouter

free_provider = FreeSearchProvider()
anysearch_provider = AnySearchProvider()
router = SearchRouter(
    bocha_client=bocha,
    free_provider=free_provider,
    anysearch_provider=anysearch_provider,
)

agent_tools = [
    WebSearchTool(bocha_client=bocha, search_router=router),  # 用 router 替代 zhipu
    ...
]
```

**验证步骤**:
```bash
# 验证: 触发一次日报生成，检查搜索质量
curl -X POST http://localhost:8765/api/reports/run -H "Content-Type: application/json" -d '{"shadow_mode": true}'
# 等待完成，检查 diagnostics
curl http://localhost:8765/api/diagnostics/last-run
```

**回滚条件**: 日报生成失败或文章数 < 改进前 → 回滚。

---

### 2.4 ScoutAgent 和 ExplorerAgent 改造

**修改文件**: `app/services/scout_agent.py`, `app/services/explorer_agent.py`

**ScoutAgent 改动**（line 76-86）:
```python
def _build_tools(self):
    bocha = BochaSearchClient()
    scraper = ScraperClient()
    free_provider = FreeSearchProvider()
    router = SearchRouter(bocha_client=bocha, free_provider=free_provider)
    return [
        CheckPoolGapsTool(),
        WebSearchTool(bocha_client=bocha, search_router=router),  # 改：加 router
        BochaAiSearchTool(bocha_client=bocha),                    # 不变
        ReadPageTool(scraper_client=scraper),
        ...
    ]
```

**ExplorerAgent 改动**（line 103-113）:
```python
def _build_tools(self):
    bocha = BochaSearchClient()
    scraper = ScraperClient()
    free_provider = FreeSearchProvider()
    router = SearchRouter(bocha_client=bocha, free_provider=free_provider)
    return [
        WebSearchTool(bocha_client=bocha, search_router=router),  # 已有 router，加 free
        ReadPageTool(scraper_client=scraper),
        ...
    ]
```

**验证步骤**: 同 2.3，触发日报后检查 scout/explorer 日志。

---

## 三、Phase 3：页面读取改造

### 3.1 Jina Reader 熔断器

**修改文件**: `app/services/jina_reader.py`

**改动位置**: `__init__`（line 89）和 `scrape()`（line 99）

**改动前**:
```python
def __init__(self, api_key=None, base_url=None):
    self.api_key = api_key or settings.jina_api_key
    self.base_url = base_url or settings.jina_base_url
    # 无熔断器
```

**改动后**:
```python
def __init__(self, api_key=None, base_url=None):
    self.api_key = api_key or settings.jina_api_key
    self.base_url = base_url or settings.jina_base_url
    from app.services.circuit_breaker import CircuitBreaker
    self._circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=300)
```

**scrape() 改动**:
```python
async def scrape(self, url, timeout=None):
    # 熔断器检查
    if self._circuit_breaker.should_skip():
        logger.info("JinaReader: circuit open, skipping to fallback for %s", url[:50])
        return await self._fallback_scrape(url, timeout)

    try:
        result = await self._jina_scrape(url, timeout)
        self._circuit_breaker.record_success()
        return result
    except Exception as exc:
        self._circuit_breaker.record_failure()
        logger.warning("JinaReader: jina failed (%s), trying fallback for %s", exc, url[:50])
        return await self._fallback_scrape(url, timeout)
```

**验证步骤**:
```bash
python -c "
import asyncio
from app.services.jina_reader import JinaReaderClient

async def test():
    jina = JinaReaderClient()
    # 正常请求
    result = await jina.scrape('https://example.com')
    assert result.get('status') in ('ok', 'error'), f'Unexpected status: {result.get(\"status\")}'
    print(f'Jina scrape: status={result.get(\"status\")}, breaker={jina._circuit_breaker.state}')

    # 模拟连续失败
    for _ in range(5):
        jina._circuit_breaker.record_failure()
    assert jina._circuit_breaker.state == 'open'
    print(f'After 5 failures: breaker={jina._circuit_breaker.state}')

    # 下次请求应跳过 Jina
    result2 = await jina.scrape('https://example.com')
    print(f'After breaker open: status={result2.get(\"status\")}')

asyncio.run(test())
"
```

---

### 3.2 修复超时层次

**修改文件**: `app/services/jina_reader.py`, `app/services/scraper.py`

**当前问题**:
- `_jina_scrape()`: 20s timeout × 2 次重试 = 最多 40s
- harness read_page timeout: 25s
- 结果：Jina 重试时 harness 已超时

**改动**:
- `_jina_scrape()` 重试次数: 2 → 1（line 147-157）
- `_jina_scrape()` timeout: 不变（由调用方传入）
- `ScraperClient._trafilatura_scrape()` timeout: 不变（由调用方传入）

**验证**: 同 3.1 的测试，确认总耗时 < 25s。

---

### 3.3 清理 ContentExtractor（可选，低优先级）

**删除文件**: `app/services/content_extractor.py`

**影响分析**: 搜索代码中无任何地方 import ContentExtractor。仅 `content_extractor.py` 内部有 `if __name__ == "__main__"` 测试代码。

**验证**:
```bash
# 确认无外部依赖
grep -r "content_extractor" app/ --include="*.py" | grep -v "__pycache__"
# 应该只看到 content_extractor.py 自身
```

---

## 四、Phase 4：端到端验证

### 4.1 Shadow Mode 测试

```bash
# 启动服务
python -m uvicorn main:app --host 0.0.0.0 --port 8765

# 触发 shadow mode 日报（不发布）
curl -X POST http://localhost:8765/api/reports/run \
  -H "Content-Type: application/json" \
  -d '{"shadow_mode": true}'

# 检查结果
curl http://localhost:8765/api/diagnostics/last-run
```

**验收标准**:
- [ ] `publish_grade` 不低于改进前
- [ ] `daily_report_score` 不低于改进前
- [ ] 文章数 >= 4
- [ ] 无 `model_fallbacks` 错误
- [ ] 搜索日志中出现 `provider: "ddg"` 或 `provider: "brave"` 来源

### 4.2 搜索成本对比

```bash
# 改进前：检查 Bocha 调用次数
curl http://localhost:8765/api/diagnostics/health?deep=true
# 记录 bocha.request_count

# 改进后：再触发一次，对比 request_count
# 预期：Ingester 部分的 Bocha 调用降为 0
```

### 4.3 结果来源多样性

```bash
# 检查搜索日志中各 provider 的结果数
grep "SearchRouter" logs/app.log | tail -20
# 预期：出现 bocha + ddg/brave/anysearch 的混合来源
```

---

## 五、回滚预案

每个 Phase 独立可回滚：

| Phase | 回滚方式 | 影响范围 |
|-------|---------|---------|
| Phase 1 | 删除新文件，不改现有代码 | 零影响 |
| Phase 2 | git revert 相关 commit | 搜索恢复旧逻辑 |
| Phase 3 | git revert 相关 commit | 页面读取恢复旧逻辑 |

**关键原则**: Phase 1 的新文件（circuit_breaker.py, free_search_provider.py, anysearch_provider.py）不被任何现有代码 import，只有 Phase 2 的改动才会引用它们。所以 Phase 1 可以安全地先部署、先验证。

---

## 六、验收清单总览

### Phase 1 验收
- [ ] `CircuitBreaker` 单元测试通过
- [ ] `FreeSearchProvider` 返回 >0 结果（中英文各 1 条）
- [ ] `AnySearchProvider` 返回 >0 结果（英文 1 条）
- [ ] `_extract_json_ld()` 正确提取标准 JSON-LD
- [ ] 无任何现有测试回归

### Phase 2 验收
- [ ] `SearchRouter.search()` 不传新参数时行为不变
- [ ] `SearchRouter.search()` 传入 free_provider 后返回结果数 >= 旧版
- [ ] Ingester 搜索日志中出现 `provider: "ddg"` 或 `"brave"`
- [ ] DailyReportAgent 日报生成成功（shadow mode）
- [ ] Bocha 日调用次数从 ~1200 降至 ~30（Ingester 部分为 0）

### Phase 3 验收
- [ ] Jina 连续失败后熔断器自动跳过
- [ ] 页面读取总耗时 < 25s（95 分位）
- [ ] JSON-LD 提取覆盖率 > 50%（有 JSON-LD 的页面）
- [ ] ContentExtractor 删除后无 import 错误

### 整体验收
- [ ] 日报生成成功率 >= 改进前
- [ ] 日报文章来源多样性提升（日志中出现多个 provider）
- [ ] 英文文章来源质量提升（出现 Plastics News/ICIS 等）
- [ ] 搜索月成本从 ~¥7.5 降至 ~¥0.25
