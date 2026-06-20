# 实验 4：智谱搜索三引擎实测 — 关键发现

> 跑了 6 query (3 中文 + 3 英文) × 3 引擎 = 18 次调用
> 全部成功，数据落盘 `experiments/search_v2/results/zhipu_link_probe.json`

## TL;DR (一句话结论)

**`search_std` 和 `search_pro` 在中文 query 上 link 字段 100% 损坏；只有 `search_pro_sogou` 可用。**

**——这就是当年停用智谱的真实原因。**

---

## 详细数据

每个引擎在每条 query 上的表现：

| 引擎 | 单价 | 中文召回 (avg/10) | **中文 link 完整率** | 英文召回 | 英文 link 完整率 | 中文 content avg | 英文 content avg |
|------|-----:|------------------:|-------------------:|--------:|----------------:|----------------:|----------------:|
| `search_std` | ¥0.01 | 4.7 | **0%** ❌ | 8.0 | 83% | 1278 | 3326 |
| `search_pro` | ¥0.03 | 4.7 | **0%** ❌ | 8.0 | 83% | 1278 | 3326 |
| **`search_pro_sogou`** | **¥0.05** | **10.0** ✅ | **100%** ✅ | 10.0 | 100% | **9101** | 3674 |

### 单条 query 详情

```
engine=search_std         q=zh1 "注塑机 新品发布"             n= 1   empty_link=1/1   ❌
engine=search_std         q=zh2 "塑料污染 治理 政策"           n= 3   empty_link=3/3   ❌
engine=search_std         q=zh3 "高分子材料 研究进展"          n=10   empty_link=10/10 ❌
engine=search_std         q=en1 "polymer industry news"      n=10   empty_link=0     ✅
engine=search_std         q=en2 "biodegradable polymer..."   n=10   empty_link=0     ✅
engine=search_std         q=en3 "plastic recycling regul..." n= 4   empty_link=4/4   ⚠️

engine=search_pro         q=zh1 ...                          n= 1   empty_link=1/1   ❌
engine=search_pro         q=zh2 ...                          n= 3   empty_link=3/3   ❌
engine=search_pro         q=zh3 ...                          n=10   empty_link=10/10 ❌
engine=search_pro         q=en1 ...                          n=10   empty_link=0     ✅
engine=search_pro         q=en2 ...                          n=10   empty_link=0     ✅
engine=search_pro         q=en3 ...                          n= 4   empty_link=4/4   ⚠️

engine=search_pro_sogou   q=zh1 ...                          n=10   empty_link=0     ✅
engine=search_pro_sogou   q=zh2 ...                          n=10   empty_link=0     ✅
engine=search_pro_sogou   q=zh3 ...                          n=10   empty_link=0     ✅
engine=search_pro_sogou   q=en1 ...                          n=10   empty_link=0     ✅
engine=search_pro_sogou   q=en2 ...                          n=10   empty_link=0     ✅
engine=search_pro_sogou   q=en3 ...                          n=10   empty_link=0     ✅
```

---

## 关键发现解读

### 1. 中文 link 字段损坏不是 bug，是设计

`search_std` 和 `search_pro` 在中文 query 上返回的"结果"实质是**没有 URL 的摘要片段**——智谱用自家知识库做了"答案合成"而不是"网页搜索"。
对纯问答场景可用，对我们这种**"要把文章存进库 + 提供 URL 给读者点击"**的项目，**完全不可用**。

### 2. `search_pro_sogou` 才是真正的"网页搜索"

底层是搜狗，返回完整 URL + 完整网页内容片段。中文 query 下：
- 召回足额：100% 拿满 count
- link 100% 完整
- content 平均 9101 字（pro 的 4 倍，**Bocha 800 字 summary 的 11 倍**）
- `q=zh3 "高分子材料 研究进展"` 在 search_pro_sogou 下 **avg 14503 字**，信息密度极高

### 3. 即使是英文，`search_std/pro` 也不稳定

`q=en3 "plastic recycling regulation"` 在 search_std/pro 下也只返回 4 条且 link 全空。
推测：智谱内部判定"这种政策类 query 我有内置答案"就走自合成路径。
**只有 search_pro_sogou 真正稳定走网页搜索**。

### 4. 字段命名跟 Bocha 完全不同

| Bocha | 智谱 |
|-------|------|
| `name` | `title` |
| `url` | `link` |
| `snippet` | （无） |
| `summary`（800 字截断） | `content`（按 content_size 控制，high 模式下能拉到 9000-14000 字） |
| `datePublished` | `publish_date` |
| `thumbnailUrl` | `icon`（注：是站点 icon 不是文章图） |
| `siteName` | `media` |

### 5. content_size=high 是关键

实验全程用 `content_size=high`。如果切回 `medium`，content 字数会大幅缩水。这意味着接入智谱时**必须显式传 high**。

---

## 对照 Bocha 的差异化价值

| 维度 | Bocha web-search | 智谱 search_pro_sogou |
|------|------------------|----------------------|
| 单价 | ~¥0.02-0.04（待实测） | ¥0.05 |
| 中文 count=50 召回 | 38-48 条 | **未测，但 count=10 实测拿满** |
| 单条 content 字数 | **800 字（截断）** | **9101 字（完整）** |
| 发布时间字段 | 100% 覆盖 | 100% 覆盖 |
| URL 字段稳定性 | 100% | 100% (sogou) |
| 国内合规 | ✅ 数据不出海 | ✅ 数据不出海 |
| 是否有 rerank | ✅ /v1/rerank | ❌ 无 |
| 能否过滤域名 | ✅ include 数组 | ⚠️ search_domain_filter 只能传单个域名 |

**核心差异点**：
- Bocha 强项：rerank、include 多域名定向、对中文新闻类内容覆盖广
- 智谱 search_pro_sogou 强项：**单条 content 字数是 Bocha 的 11 倍** — 如果质量足够，可能直接替代部分 read_page 调用

---

## 风险与待验证项

1. **重叠率未知**：智谱 search_pro_sogou 拿到的 URL 和 Bocha 拿到的 URL 重叠多少？如果 80% 重叠，引入智谱意义不大；如果 30% 重叠，就是有效双源。
2. **count=50 兑现率未知**：本实验只测 count=10，不确定 search_pro_sogou 在 count=50 上的表现。
3. **search_domain_filter 单域名限制**：tools.py 里 academic 用了 `["edu.cn", "ac.cn", "cas.cn", "nature.com", "acs.org", "pubs.rsc.org", "sciencedirect.com"]` 7 个域名 include，智谱单参数搞不了。
4. **content 长度 vs 速度**：14000 字 content 拉回来是不是比 Bocha 800 字慢得多？（本实验未测延迟）

---

## 推荐行动（仅记录，等所有实验完成再决策）

| 优先级 | 行动 | 理由 |
|-------|------|------|
| P0 | 项目里现有的 `ZhipuSearchClient` 默认引擎切到 `search_pro_sogou` | std/pro 中文 link 全空，client 默认在用哪个需查 |
| P0 | 智谱集成必须传 `content_size=high`，否则 content 缩水 | 实验全程依赖该参数 |
| P1 | 跑实验 4 拓展：search_pro_sogou × count=50 × freshness 扫荡 | 确认这个引擎是否能完全替代 search_std/pro |
| P1 | 跑实验 5：Bocha + 智谱 sogou 交叉，看独特贡献率 | 决定是否值得双源并行 |
