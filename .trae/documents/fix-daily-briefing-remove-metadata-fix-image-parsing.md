# 修复计划：每日简报洞察 + 清理前端元数据 + 修复图片解析

## 问题诊断

### 问题 1：每日简报/洞察功能缺失

**根因**：架构简化过程中，趋势综述/洞察功能被有意禁止，但未用新的替代方案补上。

具体证据：

* `WriteSectionTool` 的 prompt 硬性禁止："不要生成行业趋势综述，不要跨主题补数"（[tools.py:1492](d:\workflow_news\app\services\tools.py#L1492)）

* 合成 prompt 也限制："不要生成行业趋势综述，除非明确有两个以上高可信主题可互相支撑"（[daily\_report\_agent.py:1802](d:\workflow_news\app\services\daily_report_agent.py#L1802)）

* `allowed_for_trend_summary` 标记存在于代码中，评分也加了分，但从未触发趋势综述章节生成

* `compare_sources` 工具产出了 `trends.insight` 数据，存入 `memory.key_findings`，但最终仅用于拼接摘要文字

* "编者按"功能存在但极简：仅拼接 3 个 key\_finding，格式为"今日关注：X；Y；Z。"

* `Report.summary` 只是主题标题的简单罗列，不是分析性摘要

**修复方案**：重新实现"每日洞察"板块，作为日报的独立板块（在三个板块之后），由 LLM 基于 `compare_sources` 的趋势数据和所有入选文章生成。

### 问题 2：前端展示无用的 Agent 元数据

**根因**：前端 HeroSection 和 DashboardView 展示了面向开发者的内部质量指标（A级来源、数字证据、配图数等），对实验室师生没有阅读价值。

需要删除的前端元素：

* HeroSection.vue：第 91-95 行的三个指标 badge（"A级来源 X"、"数字证据 X"、"配图 X 张"）

* DashboardView\.vue：第 69-80 行的 qualityChips（"A级来源 · X 条"、"一手证据 · X 条"、"数字可引 · X 条"）

* CoverageGauge.vue：第 62-64 行底部的"配图: X 张"和"板块均衡度"

### 问题 3：图片解析偏向 logo

**根因**：og:image 提取是无条件最高优先级的，完全跳过了评分系统。很多中文行业网站的 og:image 就是网站 logo 或模板头图。

具体问题链：

1. [jina\_reader.py:209-213](d:\workflow_news\app\services\jina_reader.py#L209-L213)：og:image 直接提取，不过 `_score_image_src`
2. [jina\_reader.py:215-216](d:\workflow_news\app\services\jina_reader.py#L215-L216)：`if not image_url` —— 只有 og:image 为空才进评分路径
3. [jina\_reader.py:278-282](d:\workflow_news\app\services\jina_reader.py#L278-L282)：Markdown 内联图取第一张，无过滤

**修复方案**：让 og:image 也经过 `_score_image_src` 评分，如果评分低于阈值（如 < 0 分），则放弃 og:image，转而走评分路径选取内容图。

***

## 实施步骤

### 步骤 1：重新实现"每日洞察"板块

#### 1.1 修改 FinishTool，扩展"编者按"为"每日洞察"

**文件**：`app/services/tools.py`（FinishTool.execute，约 L1737-1747）

将当前的简单编者按（拼接 3 个 key\_finding）升级为调用 LLM 生成结构化的"每日洞察"摘要，包含：

* **今日重点**：2-3 句话概括当天最重要的行业动态

* **趋势信号**：从 `compare_sources` 的 trends 数据中提炼跨文章趋势

* **关注建议**：基于入选文章给出值得后续跟踪的方向

具体做法：

1. 在 FinishTool.execute 中，收集 `memory.key_findings` 和 `memory.publishable_articles()` 的关键信息
2. 构建一个 prompt，让 LLM 生成 150-200 字的"每日洞察"
3. 将结果存入 `result.data["editorial"]`（替换现有简陋编者按）
4. 添加 `result.data["daily_briefing"]` 字段存放结构化洞察

#### 1.2 修改 WriteSectionTool 的 prompt，允许生成洞察

**文件**：`app/services/tools.py`（WriteSectionTool，约 L1492）

修改规则 6：

* 从："不要生成行业趋势综述，不要跨主题补数。"

* 改为："不要在同一板块内跨主题补数。日报末尾的每日洞察由系统自动生成，不需要 write\_section 处理。"

#### 1.3 修改合成 prompt

**文件**：`app/services/daily_report_agent.py`（`_build_synthesis_prompt`，约 L1802）

修改趋势综述限制：

* 从："不要生成行业趋势综述，除非明确有两个以上高可信主题可互相支撑。"

* 改为："日报末尾会自动生成每日洞察板块，你只需关注各板块内容撰写。"

#### 1.4 修改日报持久化逻辑，在 markdown 末尾追加"每日洞察"

**文件**：`app/services/daily_report_agent.py`（约 L1956-1964）

在现有编者按逻辑之后，添加"每日洞察"板块的持久化：

* 如果 `result.daily_briefing` 存在，生成一个 `## 每日洞察` 板块追加到 markdown\_content 末尾

* 同时更新 `Report.summary`，使用更有价值的洞察摘要替代简单的标题罗列

#### 1.5 新增 LLM 调用生成洞察

**文件**：`app/services/tools.py`（FinishTool.execute 内部）

添加一个 LLM 调用，输入所有入选文章的关键信息 + trends 数据，输出结构化的每日洞察。prompt 要求：

* 基于 A/B 级来源的关键发现

* 跨文章趋势关联

* 150-200 字

* 中文输出，术语准确

***

### 步骤 2：删除前端无用的 Agent 元数据展示

#### 2.1 修改 HeroSection.vue

**文件**：`frontend/src/components/HeroSection.vue`

删除第 91-95 行的三个指标 badge：

```html
<!-- 删除这整段 -->
<div v-if="report" class="mt-4 flex flex-wrap gap-2 max-w-xl text-[11px]">
  <span ...>A级来源 {{ highTrustCount }}</span>
  <span ...>数字证据 {{ primarySignalCount }}</span>
  <span ...>配图 {{ imageCount > 0 ? `${imageCount} 张` : '未展示' }}</span>
</div>
```

同时删除对应的 computed 属性（highTrustCount、primarySignalCount、imageCount 如果仅被此模板使用）。
但 imageCount 还被第 118-119 行使用，所以保留 imageCount，只删除 highTrustCount 和 primarySignalCount。

#### 2.2 修改 DashboardView\.vue

**文件**：`frontend/src/views/DashboardView.vue`

删除第 69-80 行的 qualityChips computed 和对应模板渲染（约第 222-226 行的 `<div v-if="qualityChips.length"...>` 部分）。

#### 2.3 修改 CoverageGauge.vue

**文件**：`frontend/src/components/CoverageGauge.vue`

删除第 62-64 行底部的"配图"和"板块均衡度"信息行：

```html
<!-- 删除这整段 -->
<div class="mt-2 pt-3 border-t ...">
  <span>配图: ...</span>
  <span>板块均衡度: ...</span>
</div>
```

同时删除对应的 diversityLabel computed（如果不再使用）。

***

### 步骤 3：修复图片解析偏向 logo 的问题

#### 3.1 让 og:image 也经过评分系统

**文件**：`app/services/jina_reader.py`（`_fallback_scrape` 方法，约 L209-241）

修改逻辑：

1. 提取 og:image 后，调用 `_score_image_src(og_image_url, html)` 评分
2. 如果评分 < 0（即被扣分超过基础分），视为不可靠，设 `image_url = None`
3. 然后继续走原有的 HTML `<img>` 评分路径

修改后的伪逻辑：

```python
# Extract og:image
image_url = None
m = _OG_IMAGE_RE.search(html)
if m:
    og_candidate = m.group(1).strip()
    og_score = _score_image_src(og_candidate, html)
    if og_score >= 0:
        image_url = og_candidate

# Fallback: score all images, pick the best
if not image_url:
    # ... 原有的评分逻辑不变
```

#### 3.2 让 Markdown 内联图也经过评分

**文件**：`app/services/jina_reader.py`（约 L278-282）

修改 Markdown 内联图提取逻辑：

1. 提取所有 Markdown 内联图（不只是第一张）
2. 对每张调用 `_score_image_src` 评分（如果 HTML 可用）
3. 选分数最高的，而非第一张

修改后的伪逻辑：

```python
if not image_url and markdown:
    md_images = re.findall(r'!\[.*?\]\((https?://[^\)]+)\)', markdown)
    if md_images and html:
        candidates = [(score, src) for src in md_images if (score := _score_image_src(src, html)) > -999]
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            image_url = candidates[0][1]
    elif md_images:
        image_url = md_images[0]
```

#### 3.3 同步修改 Jina API 抓取路径中的图片提取

**文件**：`app/services/jina_reader.py`（`_jina_scrape` 方法）

检查 Jina API 返回的图片是否也需要类似处理。如果 Jina 返回了 og:image，也应经过评分。

#### 3.4 同步修改 scraper.py 中的 og:image 提取

**文件**：`app/services/scraper.py`（`_extract_html_meta` 函数，约 L69-73）

该函数也直接提取 og:image，但它是辅助函数，返回字典让调用方决定。需要确认调用方是否也需要加评分逻辑。如果 `SearchImagesTool` 使用了此函数的返回值，也需要在其调用方加评分。

***

### 步骤 4：验证

* 触发一次日报生成，验证：

  1. 日报末尾出现"每日洞察"板块，内容为有深度的分析性摘要
  2. 前端 HeroSection、DashboardView、CoverageGauge 不再显示 A级来源/数字证据/配图等内部指标
  3. 生成的日报配图不再出现 logo 类图片

* 运行现有测试确保无回归：`python -m pytest tests/ -v`

