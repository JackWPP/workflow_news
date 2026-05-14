# 修复计划：图片解析 + 主链路文章数量

## 问题诊断

### 问题 1：配图解析不出来

**核心发现**：之前的修改引入了一个 **致命 Bug**——`jina_reader.py` 中新增的 `logger.debug()` 调用使用了未定义的 `logger` 变量，会导致 `NameError` 异常，使整个 `_fallback_scrape` 图片提取路径崩溃。

**具体代码位置**：

* [jina\_reader.py:233](d:\workflow_news\app\services\jina_reader.py#L233) — `logger.debug("og:image rejected by scoring: %s score=%d", ...)` — `logger` 未定义

* [jina\_reader.py:261](d:\workflow_news\app\services\jina_reader.py#L261) — `logger.debug("Image scoring: best=...")` — 同上

文件头部没有 `import logging` 和 `logger = logging.getLogger(__name__)`。当 og:image 评分 < 0 时，异常被外层 try/except 捕获，整个 scrape 返回空结果——不仅图片丢失，**连 markdown 内容也一起丢了**。

**次要问题**：

* `content_extractor.py` 的 `_try_direct_http` 路径对 og:image 没有做评分（与其他路径不一致），可能接受 logo

* Jina API 路径中评分传的 `html=""`，`<article>/<main>` 位置加分逻辑失效

**关于 markitdown 的评估**：

* MarkItDown 是微软的 HTML→Markdown 转换工具，但它**不提供独立的图片列表提取**——它的图片处理仅限于将 HTML `<img>` 转为 markdown 内联图片 `![](url)`

* 我们的 `_score_image_src` + 正则提取方案实际上**已经覆盖了 MarkItDown 的图片提取能力**，而且多了一层评分过滤

* MarkItDown 的主要价值在于**内容提取质量**（HTML→Markdown 转换），而非图片提取

* **结论**：不建议用 MarkItDown 替换 Jina/Trafilatura，因为当前问题根因是 Bug 而非架构问题。修复 logger 后图片提取应该恢复正常

### 问题 2：无法稳定达到 5 篇及以上文章

发现 **3 个核心瓶颈**：

**瓶颈 #1（最严重）：`compiled_topics`** **断裂导致** **`write_section`** **完全无法工作**

* `write_section` 的 `execute()` 方法（[tools.py:1445](d:\workflow_news\app\services\tools.py#L1445)）依赖 `memory.get_compiled_topics(section)`

* `cache_compiled_topics` 只在 [daily\_report\_agent.py:1231](d:\workflow_news\app\services\daily_report_agent.py#L1231) 被调用，而该方法位于 `_compile_section_topics()` 内部

* **`_compile_section_topics()`** **在当前代码中没有被任何地方调用**（它是旧架构 Phase 1/2/3 的残留）

* 结果：`memory.compiled_topics` 永远为空 → `write_section` 永远返回失败 → Agent 的板块写作步骤全部失败 → 只能走 `finish` 工具的裸文章列表路径

**瓶颈 #2：检查点步数过早催促写作，限制了文章收集量**

* Checkpoint 2 在第 16 步就强制开始写作，此时最多评估 3-5 篇文章

* Checkpoint 3.5 在第 28 步强制 auto\_finish（只要写了内容或有 2 篇文章）

* 每篇文章需要 search + read + evaluate 的 3 步串行链路，28 步最多产出 \~9 篇（理想情况），实际 4-6 篇

* 如果搜索质量差（硬过滤拒绝多），可能只有 1-3 篇

**瓶颈 #3：多层硬过滤的漏斗效应**

```
搜索结果 → read_page 过滤(page_kind/recency/publish_block) → evaluate_article 过滤(时效/硬拒绝/LLM判定) → 最终文章
```

* `read_page` 中 `classify_source()` 会将 download/anti\_bot/binary/search/navigation/product/about/homepage 类页面标记为 D 级并硬拒绝

* 7 天时效硬拒绝（read\_page 和 evaluate\_article 都有）

* 20+ 个负面关键词的硬拒绝规则

* 每层折损 30-50% 的候选

***

## 实施步骤

### 步骤 1：修复 jina\_reader.py 的 logger 缺失 Bug（最高优先级）

**文件**：`app/services/jina_reader.py`

1. 在文件头部添加 `import logging` 和 `logger = logging.getLogger(__name__)`
2. 这会修复 `NameError` 导致的整个 scrape 路径崩溃问题

### 步骤 2：修复 content\_extractor.py 的 direct HTTP 路径缺少 og:image 评分

**文件**：`app/services/content_extractor.py`

1. 在 `_try_direct_http` 方法中（约 L241-244），对 og:image 添加 `_score_image_src` 评分验证
2. 与其他路径保持一致，拒绝低分 og:image

### 步骤 3：修复 compiled\_topics 断裂问题（关键）

**文件**：`app/services/tools.py`（WriteSectionTool.execute）

修改 `write_section` 的逻辑：当 `compiled_topics` 为空时，不再直接返回失败，而是从 `memory.publishable_articles()` 按 section 过滤来动态构建写作素材。

具体做法：

1. 先查 `memory.get_compiled_topics(section)`
2. 如果为空，从 `memory.publishable_articles()` 中按 `article.section == section` 过滤
3. 将这些文章转化为 topics 格式（`title`, `source_tier`, `source_reliability_label`, `evidence_strength`, `supports_numeric_claims`, `facts`, `citations`）
4. 如果仍为空（该板块确实没有文章），返回失败

### 步骤 4：调整检查点步数，给 Agent 更多收集时间

**文件**：`app/services/agent_core.py`

调整检查点位置，让 Agent 有更多步骤收集文章：

| 检查点            | 旧步数      | 新步数      | 说明                           |
| -------------- | -------- | -------- | ---------------------------- |
| Checkpoint 0   | step==5  | step==5  | 保持（强制阅读仍必要）                  |
| Checkpoint 1   | step==10 | step==12 | 延后 2 步，多给一轮搜索+阅读             |
| Checkpoint 1.5 | step==12 | step==15 | 板块多样性检查延后                    |
| Checkpoint 2   | step==16 | step==22 | 延后 6 步，允许更多评估（核心改动）          |
| Checkpoint 3   | step==22 | step==30 | 催促 finish 延后                 |
| Checkpoint 3.5 | step>=28 | step>=38 | auto\_finish 延后，给 Agent 更多时间 |

调整后 Agent 可以在 22 步前自由收集+评估文章，22-30 步写板块，30-38 步收尾。理论上 22 步前可以评估 6-8 篇文章。

### 步骤 5：降低 auto\_finish 的文章数门槛

**文件**：`app/services/agent_core.py`

当前 Checkpoint 3.5 在 `step >= 28` 且 `articles >= 2` 时就强制 auto\_finish。修改为：

* 仅当 `articles >= 4` 时才允许 auto\_finish

* 如果 `articles < 4` 但步数/时间还够，不强制退出（让 Agent 继续搜集）

* 如果步数/时间真的耗尽，走 budget\_exhausted/timeout 路径的兜底结果

### 步骤 6：验证

* 触发一次日报生成，验证：

  1. 配图能正常解析出来
  2. `write_section` 不再返回"没有足够高可信主题"
  3. 日报包含 5+ 篇文章
  4. 日报末尾有"每日洞察"板块

* 运行测试确保无回归：`python -m pytest tests/ -v --ignore=tests/test_agent_services.py`

