# 检索与信源体系深度方案（2026-06-13）

## 0. 结论先行

当前平台的问题不只是 RSS 少，也不是搜索词不够多，而是“发现、筛选、证据、写作”之间缺少一层可解释的信源治理。建议把系统升级为：

```
稳定源 RSS/API + 高价值 listing 适配器 + 结构化搜索模板
  -> ArticlePool(raw + discovery metadata + source quality)
  -> deterministic candidate ranking
  -> Agent 只做阅读、判断、归纳、写作
  -> EvidencePack + publish gate
```

核心目标：

- 广度：每天覆盖高材制造、清洁能源、AI for materials 三条线，而不是只靠 Bocha 的自然排序。
- 深度：每条日报至少有 1 个主证据源，必要时附 1-2 个辅助源，不让 C 类市场报告站成为主角。
- 信源质量：A/B 源成为日报主体，C 源只做背景，D 源不入池或不进入 Agent。
- 对使用者帮助：从“新闻摘要”升级为“研究情报”，给出为什么重要、适合谁看、可以追踪什么后续。

## 1. 现状判断

### 1.1 当前链路的真实重心

主日报触发路径实际更依赖 `EditorAgent` 从 `ArticlePool` 选种子，而不是让 `DailyReportAgent/AgentCore` 从零自主搜索。因此，信息检索的质量短板首先出现在：

- 入池结果是否有足够高质量的 A/B 源；
- 入池记录是否保留“它为什么被发现”的 metadata；
- `EditorAgent` 是否按 section/category/source/domain 做平衡；
- 低质源是否在 Agent 阅读之前被挡住。

### 1.2 当前主要问题

| 层面 | 问题 | 后果 |
| --- | --- | --- |
| 广度 | 搜索模板宽泛，AI 线容易混入通用 AI 新闻 | 看似覆盖三方向，实际主题漂移 |
| 深度 | 缺少主源/辅源配对机制 | 报道停留在复述，缺少可信支撑 |
| 信源质量 | 市场报告站、B2B、SEO 页容易入池 | 报告可信度波动大 |
| 可解释性 | 搜索 query、意图、来源类型未完整传递 | 事后很难解释为什么选这篇 |
| 学术通道 | RSS 能拿到期刊目录，但不能表达关键词意图 | 相关性筛选压力转移给 Agent |
| 中文通道 | 中文 RSS 稀缺，行业站多为 listing/搜索页 | 需要适配器而非继续找“神奇 RSS” |
| 用户帮助 | 日报少了“对实验室有什么用”的字段 | 师生难以把信息转成行动 |

## 2. 信源分层模型

### 2.1 五级角色

| 级别 | 典型来源 | 用途 | 入选策略 |
| --- | --- | --- | --- |
| S/A 主证据 | 政府、标准、期刊、大学/实验室、企业 newsroom | 支撑关键事实 | 可作为日报主条目 |
| B 专业源 | 行业协会、展会官方媒体、工程媒体、垂直媒体 | 产业上下文 | 可入选，但需配额 |
| C 辅助源 | 市场报告、财经转载、博客、公众号桥接 | 观察信号 | 不建议单独主导条目 |
| D 阻断源 | B2B 商城、SEO 采集、纯营销、下载页 | 无 | 不入池或不进入 Agent |
| Watch-only | 价格行情、招聘、展商目录、产品目录 | 监控指标 | 不进入日报正文 |

### 2.2 日报发布门槛

- A/B 源占比 >= 60%。
- C 源占比 <= 25%。
- 单域名每日最多 1-2 条。
- 每个板块至少 1 条 A/B 源，否则该板块降级为“观察”。
- 若 markdown 太短、只剩占位文案、或所有入选条目均为 C 源，则不得 `complete_auto_publish`。

## 3. RSS/API/Listing 信源方案

### 3.1 可直接保留或新增的 RSS

以下为 2026-06-13 本地探测结果，判定标准为 HTTP 可达且 `feedparser` 可解析出 entries。

| 源 | URL | 结果 | 建议 |
| --- | --- | --- | --- |
| Nature Materials current | `http://feeds.nature.com/nmat/rss/current` | 200 / 8 entries | 保留，A |
| Nature materials science subject | `https://www.nature.com/subjects/materials-science.rss` | 200 / 30 entries | 新增，A，需关键词过滤 |
| Nature polymer chemistry subject | `https://www.nature.com/subjects/polymer-chemistry.rss` | 200 / 30 entries | 新增，A |
| ACS Macro Letters | `https://pubs.acs.org/action/showFeed?jc=amlccd&type=etoc&feed=rss` | 200 / 19 entries | 保留，A |
| Macromolecules | `https://pubs.acs.org/action/showFeed?jc=mamobx&type=etoc&feed=rss` | 200 / 42 entries | 新增，A |
| Biomacromolecules | `https://pubs.acs.org/action/showFeed?jc=bomaf6&type=etoc&feed=rss` | 200 / 50 entries | 新增，A |
| ACS Applied Polymer Materials | `https://pubs.acs.org/action/showFeed?jc=aapmcd&type=etoc&feed=rss` | 200 / 95 entries | 新增，A，高相关 |
| ACS Polymers Au | `https://pubs.acs.org/action/showFeed?jc=apaccd&type=etoc&feed=rss` | 200 / 21 entries | 新增，A |
| ACS Sustainable Chemistry & Engineering | `https://pubs.acs.org/action/showFeed?jc=ascecg&type=etoc&feed=rss` | 200 / 30 entries | 保留/修正参数，A |
| ScienceDirect Polymer | `https://rss.sciencedirect.com/publication/science/00323861` | 200 / 100 entries | 保留，A |
| ScienceDirect Additive Manufacturing | `https://rss.sciencedirect.com/publication/science/22148604` | 200 / 31 entries | 保留，A |
| Journal of Membrane Science | `https://rss.sciencedirect.com/publication/science/03767388` | 200 / 72 entries | 新增，清洁能源线 |
| MIT Materials | `https://news.mit.edu/rss/topic/materials-science` | 200 / 50 entries | 保留，A/B |
| Berkeley Lab News | `https://newscenter.lbl.gov/feed/` | 200 / 12 entries | 保留，A/B，需材料关键词过滤 |
| ScienceDaily Materials | `https://www.sciencedaily.com/rss/matter_energy/materials_science.xml` | 200 / 60 entries | 可用，B，作为发现源 |

### 3.2 不建议作为普通 RSS 的源

| 源 | 探测结果 | 建议 |
| --- | --- | --- |
| arXiv `https://rss.arxiv.org/rss/cond-mat.mtrl-sci` | 200 但 entries=0 | 改用 arXiv API keyword query |
| Phys.org 当前 materials URL | 404 | 暂不加入，后续找站内准确 RSS |
| `https://www.gaofenzi.org/feed` | SSL 证书过期 | 可作为 listing/direct HTTP 特例，不作为稳定 RSS |
| 微信 feeddd | 历史返回 HTML/不稳定 | 仅 watch，不作为主链路 |
| 中文政府/行业媒体 RSS | 多数不存在或失效 | 做 listing 适配器/站内搜索 |

### 3.3 API 通道

| API | 适用方向 | 价值 | 接入方式 |
| --- | --- | --- | --- |
| arXiv API | AI for materials、polymer ML、soft matter | 可用关键词和分类组合，避免粗 RSS 噪音 | `search_query=(all:polymer AND all:\"machine learning\") AND cat:cond-mat.mtrl-sci` |
| Crossref Works | DOI 元数据、期刊新文章补齐 | 可验证 DOI、期刊、发布日期 | keyword + from-pub-date |
| OpenAlex Works | 学术主题扩展和引用上下文 | 可做概念/机构/作者维度聚合 | concepts + date filters |
| PubMed / Europe PMC | 生物基、医用高分子、生物材料 | 覆盖 biomedical polymer | 只接窄关键词，避免医学泛化 |

优先级：arXiv API > Crossref/OpenAlex > PubMed/Europe PMC。

## 4. 三条业务方向的检索设计

### 4.1 高材制造

目标不是“所有塑料新闻”，而是加工工艺、装备、材料体系和规模化应用。

核心子方向：

- 注塑、挤出、吹塑、发泡、薄膜、纺丝、复合成型；
- 改性塑料、工程塑料、聚烯烃、弹性体、热塑复材；
- 3D 打印/增材制造、自动化、过程控制、质量检测；
- 企业产线、设备交付、规模化项目。

搜索模板建议：

- 中文 primary/company：`注塑 设备 新品 site:haitian.com OR site:yizumi.com`
- 中文 industry：`高分子材料 加工 技术 产业化`
- 中文 application：`改性塑料 汽车 轻量化 量产`
- 英文 academic：`polymer processing rheology extrusion injection molding`
- 英文 industry：`polymer processing equipment extrusion injection molding new technology`

### 4.2 清洁能源

核心不是泛新能源，而是与高分子/材料加工相关的能源材料和循环体系。

核心子方向：

- 电池隔膜、粘结剂、固态电解质、离子交换膜；
- 光伏封装、氢能膜材料、碳捕集膜；
- 塑料回收、化学回收、再生料标准；
- 生物基/可降解材料与碳足迹政策。

搜索模板建议：

- 中文 policy：`塑料回收 政策 标准 碳足迹`
- 中文 industry：`电池隔膜 高分子 材料 扩产`
- 英文 academic：`polymer electrolyte membrane battery separator recycling`
- 英文 policy：`plastic recycling regulation chemical recycling standard`

### 4.3 AI for Materials

必须限制为“AI + 材料/高分子/制造”，明确排除通用大模型、AI 应用软件、互联网 AI。

核心子方向：

- polymer informatics、property prediction、formulation optimization；
- autonomous lab、self-driving lab、Bayesian optimization；
- injection molding/extrusion digital twin、defect detection、process control；
- generative models for materials discovery。

搜索模板建议：

- arXiv/API：`polymer machine learning property prediction`
- arXiv/API：`materials informatics polymer`
- web primary：`autonomous laboratory polymer synthesis`
- web manufacturing：`injection molding machine learning process optimization`
- web manufacturing：`extrusion digital twin polymer`

排除词：

- `chatbot`, `AI assistant`, `AI chip`, `AI phone`, `marketing automation`, `general artificial intelligence`

## 5. 检索系统改造方案

### 5.1 Source Registry

把 `app/seed.py` 里的源拆成可维护 registry：

```yaml
id: acs_applied_polymer_materials
name: ACS Applied Polymer Materials
kind: rss
url: https://pubs.acs.org/action/showFeed?jc=aapmcd&type=etoc&feed=rss
tier: A
sections: [academic]
categories: [高材制造, 清洁能源]
language: en
include_any: [polymer, macromolecule, processing, membrane, recycling]
exclude_any: [editorial board, cover image, correction]
lead_allowed: true
quota_per_day: 2
```

这样可以把信源治理从代码列表变成配置和测试。

### 5.2 Discovery Metadata

每条入池记录必须保存：

- `discovery_channel`: `rss` / `api` / `listing` / `search`
- `source_id`
- `search_query`
- `query_family`
- `intended_section`
- `intended_category`
- `source_tier`
- `source_kind`
- `page_kind`
- `lead_allowed`
- `why_discovered`

这部分应写入 `ArticlePool.eval_metadata.discovery`，并同步常用字段到 `section/category/language/source_type`。

### 5.3 Candidate Ranking

在 Agent 前做确定性重排：

```
score =
  source_tier_score
  + topicality_score
  + freshness_score
  + section/category intent score
  + primary-source bonus
  + source diversity bonus
  - low-value/domain penalty
  - watch-only penalty
```

重排目标：

- 每天候选 20 条；
- 每个 section 至少 5 条候选；
- 每个 category 至少 4 条候选；
- 单域名最多 2 条；
- C 源最多 4 条进入 Agent；
- D/watch-only 不进入 Agent。

### 5.4 EvidencePack

日报条目不应只保存最终中文段落，还应保存证据包：

```json
{
  "claim": "某公司宣布建设高分子材料回收产线",
  "primary_source": "company newsroom / government / journal",
  "supporting_sources": ["trade media", "policy page"],
  "quotes_or_excerpts": [],
  "risk": "C source only / date uncertain / translated title",
  "follow_up_keywords": ["chemical recycling", "polymer recycling line"]
}
```

这会明显提升研究助手后续问答质量。

## 6. 用户价值层改造

日报每条建议增加四个字段：

| 字段 | 意义 |
| --- | --- |
| `为什么重要` | 对实验室方向、产业趋势或政策风险的意义 |
| `可信度` | A/B/C + 主证据说明 |
| `适合谁看` | 高材制造 / 清洁能源 / AI / 课题组管理 |
| `后续追踪` | 可订阅的公司、论文关键词、政策条款或标准号 |

研究助手建议增加：

- “围绕这条日报继续查论文/专利/政策”；
- “只看 A 级来源”；
- “过去 30 天某主题趋势”；
- “把本周高材制造信号整理成组会汇报”。

## 7. 分阶段实施

### P0：已经开始/应立即完成

- 保留搜索 query 的 section/category/query_family metadata。
- `EditorAgent` seed 选择改成按来源质量、领域相关性、section/category、域名多样性重排。
- 阻断 `cir.cn`、`chinabgao`、`51sole`、`foodmate` 等低质/营销/聚合域。
- 发布门槛增加 markdown substantial check 和 all-C-source gate。
- 增加 RSS/API 探测脚本，定期输出可用性报告。

### P1：一周内

- 把 `app/seed.py` 拆为 `config/sources.yaml`，新增 source registry loader。
- 新增 arXiv API ingester，使用关键词而不是分类 RSS。
- 新增 ACS/ScienceDirect/Nature subject feeds，并删除或禁用失效源。
- 对历史 `ArticlePool` 做 backfill：补 `source_tier/source_kind/page_kind/lead_allowed`。
- 搜索模板从硬编码函数迁移到 `config/search_templates.yaml`，并支持 query metadata。

### P2：两周内

- 做中文 listing adapters：金发科技、海天、伊之密、万华、中石化/中石油新闻页、工信部/生态环境部政策列表。
- 接 Crossref/OpenAlex 做 DOI/期刊元数据校验。
- 实现 EvidencePack 表或 JSON 字段。
- 增加“高材制造/清洁能源/AI for materials”三方向 watch topic 配置。

### P3：一个月内

- 建立 100 条离线评测集，标注应入选/不应入选。
- ResearchAgent 接入语义检索，支持按来源等级、时间、方向过滤。
- 每周自动生成“信源健康报告”：可达率、入选率、被用户点踩率、C/D 源命中率。
- 做用户反馈闭环：点踩某条后自动记录 domain/topic/source_kind，影响后续排序。

## 8. 验收指标

| 指标 | 当前风险 | 目标 |
| --- | --- | --- |
| 日报 A/B 源占比 | 不稳定 | >= 60% |
| C 源主导条目 | 偶发严重 | <= 25%，且不能全 C |
| 单域名集中度 | 偶发集中 | top domain <= 2 items |
| 三方向覆盖 | 依赖搜索自然结果 | 每方向至少 1-2 条候选 |
| 搜索可解释性 | 弱 | 每条入池有 query_family/intent |
| RSS 可用性 | 有 404/403 噪音 | 每日健康检查，失败源自动降权 |
| AI 线漂移 | 容易泛 AI | AI 条目必须含 materials/polymer/manufacturing 证据 |
| 用户可行动性 | 摘要化 | 每条有 why/follow-up |

## 9. 建议的最终信息架构

```
SourceRegistry
  ├─ RSSSourceAdapter
  ├─ ApiSourceAdapter(arXiv/Crossref/OpenAlex)
  ├─ ListingSourceAdapter(company/gov/media)
  └─ SearchTemplateAdapter

DiscoveryResult
  ├─ raw title/url/snippet/content
  ├─ discovery metadata
  └─ source quality metadata

ArticlePool
  ├─ deterministic dedup
  ├─ source quality backfill
  └─ candidate ranking

EditorAgent / AgentCore
  ├─ read/evaluate/compare/write
  └─ EvidencePack

Report + ResearchAssistant
  ├─ daily intelligence
  ├─ follow-up topics
  └─ source-aware Q&A
```

## 10. 关键取舍

- RSS 是稳定英文/学术源，不是中文行业信息的主解法。
- arXiv 应走 API，不走粗分类 RSS。
- 中文行业信息靠 listing 适配器 + 搜索，不要继续寻找不存在的 RSS。
- Agent 应该少做“找信息”，多做“读证据、判断价值、写给实验室用户”。
- 信源质量必须前置到入池和 seed ranking，而不是等 Agent 写完再补救。

