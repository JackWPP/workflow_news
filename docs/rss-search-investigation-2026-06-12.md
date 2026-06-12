# RSS 源与搜索方案调研报告

> 日期：2026-06-12
> 状态：调研完成，待决策

---

## 一、问题背景

启动项目时日志中大量出现 RSS feed 失败警告：

```
WARNI [app.services.ingester] RSS feed failed for 中国石化新闻: 404 Not Found
WARNI [app.services.ingester] RSS feed failed for Plastics News: 403 Forbidden
WARNI [app.services.ingester] RSS feed failed for CompositesWorld: 403 Forbidden
...
```

需要评估：RSS 方案是否有根本性问题，是否需要重构，还是只需要优化源。

---

## 二、ArticlePool 实际数据（2026-06-12 实测）

| 来源 | 文章数 | 占比 | 语言 |
|------|--------|------|------|
| Bocha 模板搜索 | 99 | **88%** | zh 70 + en 29 |
| RSS | 14 | **12%** | 全英文 |
| **总计** | 113 | 100% | zh 62% + en 38% |

**核心发现：Bocha 搜索已经是信息获取的绝对主力（88%），RSS 仅贡献 12% 且全为英文学术文章。**

---

## 三、RSS 源可用性实测（三轮共 39 个 URL）

### 3.1 可用源（16+ 个）

| 分类 | 源 | 格式 | 备注 |
|------|-----|------|------|
| Nature 系列 | Materials, Energy, Sustainability, Chemistry, Communications, Nanotechnology, Reviews Materials | RDF | feedparser 可解析 |
| ACS 系列 | Macro Letters, Sustainable Chem Eng 等 | XML | 多个期刊可用 |
| ScienceDirect | Polymer, Additive Manufacturing, Polymer Degradation, Progress in Polymer Science, Reactive & Functional Polymers | RSS | Elsevier 旗下 |
| 综合期刊 | Science Magazine, PNAS | XML | |
| arXiv | cond-mat, cond-mat.mtrl-sci, cond-mat.soft, physics.chem-ph | RSS | **新发现**，高分子/材料高度相关 |
| 其他 | MIT Materials, Phys.org Materials, Science Daily Materials | RSS | |
| 行业 | Chemical Engineering Magazine | RSS | |

### 3.2 失效源（10 个，需删除）

| 源 | 问题 | 类型 |
|-----|------|------|
| 中国石化新闻 | 404 Not Found | RSS URL 已失效 |
| American Chemistry Council | 404 Not Found | RSS URL 已失效 |
| 国家统计局 RSS | 404 Not Found | RSS URL 已失效 |
| C&EN News | 404 Not Found | RSS URL 已失效 |
| Plastics News | 403 Forbidden | 反爬屏蔽 |
| CompositesWorld | 403 Forbidden | 反爬屏蔽 |
| Plastics Europe | 403 Forbidden | 反爬屏蔽 |
| Plastics Technology | 403 Forbidden | 反爬屏蔽 |
| 中国工业和信息化部 | 403 Forbidden | 非 RSS 页面 |
| Plastics Today | 403 Forbidden | 反爬屏蔽 |

### 3.3 完全不可用的渠道

| 渠道 | 状态 | 原因 |
|------|------|------|
| 中文政府网站（工信部/统计局/新华社） | 无 RSS | 不提供 RSS |
| 中文行业媒体（化工报/慧聪/石化） | 无 RSS 或已下线 | 国内媒体基本不提供 RSS |
| 微信桥接（feeddd.org） | JS 跳转页面 | 返回 HTML 而非 RSS |
| RSSHub 公共实例（rsshub.app） | 403 | 公共实例被广泛封禁 |
| RSC 期刊（Green Chemistry 等） | 404 | RSC 已不提供 RSS |
| Cell Press（Joule/Matter/iScience） | 403 | 需要认证 |
| 行业媒体（Rubber World/Packaging World） | 404/timeout | RSS 已下线 |

### 3.4 RSSHub 自建可行性

- 测试 `rssforever.com`（第三方 RSSHub 实例）：返回有效 XML
- 自建 RSSHub 需要 VPS + 维护，运维成本较高
- 可桥接的中文源：36kr、知乎热榜、少数派等（非高分子行业垂直内容）

---

## 四、Bocha 搜索实测

### 4.1 中文查询表现

| 查询 | 结果数 | 来源质量 |
|------|--------|---------|
| 高分子材料 加工 注塑 最新进展 | 5 | 科学网博客、中科院（中等） |
| 塑料 行业新闻 中国 2026 | 5 | 行业报告网站（中等） |
| 碳中和 塑料 回收 政策 | 5 | 政府法规、财经媒体（较好） |
| polymer processing industry news | 5 | 行业报告网站（中等） |

### 4.2 问题

- **Rate Limit**：英文查询触发 429 错误（并发过高时）
- **内容质量**：返回结果偏向行业报告/财经分析，真正的技术新闻较少
- **重复率**：同一查询可能返回同一网站的不同页面

### 4.3 现有模板搜索查询

**中文（13 个）：**
```
注塑机 新品发布, 挤出设备 技术升级, 高分子材料 产能扩建,
塑料原料 价格行情, 复合材料 汽车轻量化, 改性塑料 应用,
限塑令 最新政策, 碳关税 塑料行业, 环保法规 高分子材料,
高分子改性 研究进展, 聚合物 新材料 论文,
北京化工大学 英蓝实验室 高分子 研究, 英蓝云展 高分子材料 加工技术
```

**英文（8 个）：**
```
injection molding machine new product, polymer processing equipment innovation,
plastics recycling technology breakthrough, EU plastic regulation policy,
carbon border tax polymer industry, polymer composite materials science research,
Beijing University Chemical Technology polymer processing, Yinglan laboratory polymer research
```

---

## 五、MiniMax 搜索补充发现

通过 MiniMax MCP 搜索发现的额外信息：

- 高分子领域学术论坛活跃（Polymer Science & Technology 线上论坛）
- 中国高分子材料企业数量超 86,000 家（2024 年数据）
- 行业报告类网站（chinabgao.com、cir.cn）是中文行业信息的主要来源
- arXiv CLI 已开源（智源），可用于更精细的 arXiv 论文筛选

---

## 六、建议方案

### 不需要做的事

- ❌ 重构 ingester 架构（架构正确，失败隔离完善）
- ❌ 引入 RSSHub 自建（运维成本高，收益不确定）
- ❌ 大量扩充 RSS 源（英文行业媒体基本都被封/下线）

### 需要做的事

| 优先级 | 事项 | 预期效果 |
|--------|------|---------|
| P0 | 删除 10 个失效 RSS 源 | 消除启动 warning 噪音 |
| P1 | 新增 arXiv 4 个分类 RSS | 学术论文时效性最强，覆盖高分子/材料/化学 |
| P1 | 确认 Nature RDF 解析正常 | 7 个 Nature 源可正常使用 |
| P2 | 扩充 Bocha 中文搜索模板 | 覆盖更多细分领域（新能源材料、生物降解、3D 打印等） |
| P2 | 扩充 Bocha 英文搜索模板 | 覆盖英文行业新闻（替代被封的 RSS 源） |
| P3 | 添加更多 ScienceDirect 期刊 RSS | Polymer Chemistry, European Polymer Journal 等 |

### 信息获取架构（优化后）

```
RSS（英文学术，~20 个可靠源）───┐
                                 ├──→ ArticlePool → AgentCore
Bocha 搜索（中英文行业/政策）───┘
  - 中文：扩充到 20+ 查询模板
  - 英文：扩充到 15+ 查询模板
  - 控制并发避免 429
```

---

## 七、关键数据源清单（优化后推荐保留）

### RSS 源（保留 + 新增）

| # | 源 | URL | 状态 |
|---|-----|-----|------|
| 1 | Nature Materials | http://feeds.nature.com/nmat/rss/current | 保留 |
| 2 | Nature Energy | http://feeds.nature.com/nenergy/rss/current | 保留 |
| 3 | Nature Sustainability | http://feeds.nature.com/natsustain/rss/current | 保留 |
| 4 | Nature Chemistry | http://feeds.nature.com/nchem/rss/current | **新增** |
| 5 | Nature Communications | http://feeds.nature.com/ncomms/rss/current | **新增** |
| 6 | Nature Nanotechnology | http://feeds.nature.com/nnano/rss/current | **新增** |
| 7 | Nature Reviews Materials | http://feeds.nature.com/natrevmats/rss/current | **新增** |
| 8 | ACS Macro Letters | pubs.acs.org/.../amlccd | 保留 |
| 9 | ACS Sustainable Chem Eng | pubs.acs.org/.../ascecg | **新增** |
| 10 | Polymer (ScienceDirect) | rss.sciencedirect.com/.../00323861 | 保留 |
| 11 | Additive Manufacturing | rss.sciencedirect.com/.../22148604 | **新增** |
| 12 | Polymer Degradation | rss.sciencedirect.com/.../01413910 | **新增** |
| 13 | Progress in Polymer Science | rss.sciencedirect.com/.../00796700 | **新增** |
| 14 | Reactive & Functional Polymers | rss.sciencedirect.com/.../13815148 | **新增** |
| 15 | Science Magazine | science.org/.../science | **新增** |
| 16 | PNAS | pnas.org/.../pnas | **新增** |
| 17 | MIT Materials Science | news.mit.edu/rss/topic/... | 保留 |
| 18 | Phys.org Materials | phys.org/rss-feed/... | **新增** |
| 19 | Science Daily Materials | sciencedaily.com/rss/... | **新增** |
| 20 | arXiv cond-mat | arxiv.org/rss/cond-mat | **新增** |
| 21 | arXiv cond-mat.mtrl-sci | arxiv.org/rss/cond-mat.mtrl-sci | **新增** |
| 22 | arXiv cond-mat.soft | arxiv.org/rss/cond-mat.soft | **新增** |
| 23 | arXiv physics.chem-ph | arxiv.org/rss/physics.chem-ph | **新增** |
| 24 | Chemical Engineering | chemengonline.com/rss | **新增** |

### 删除的源（10 个）

中国石化新闻、American Chemistry Council、国家统计局、C&EN News、Plastics News、CompositesWorld、Plastics Europe、Plastics Technology、工信部、Plastics Today

---

## 八、待讨论决策

1. **Bocha 搜索模板扩充策略**：是否需要按领域细分（新能源/生物降解/3D 打印等），还是保持宽泛？
2. **arXiv 论文过滤**：arXiv 4 个分类每天可能 50-100 篇新论文，是否需要额外过滤机制？
3. **RSS 与搜索的去重**：同一研究可能在 arXiv + 期刊 RSS + 搜索结果中同时出现，当前 MinHash 去重是否足够？
4. **中文行业信息获取**：RSS 路线已死，是否考虑其他渠道（如 RSSHub 自建、行业网站定时爬取）？
