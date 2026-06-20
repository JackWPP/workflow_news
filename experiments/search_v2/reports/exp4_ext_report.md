# 实验 4 拓展：智谱 search_pro_sogou 全参数扫荡 + Bocha 重叠率

> 跑了 6 query × 3 count × 4 freshness = 72 次智谱调用
> 全部成功，且与 Bocha URL 重叠率竟然是 **0%**

## TL;DR

1. **智谱 sogou 与 Bocha URL 重叠率 = 0%（含域名级 0%）** ⚡
2. 智谱 sogou count 100% 兑现，link 100% 完整
3. 中文 content 平均 8457 字（Bocha 800 字截断的 10.6 倍）
4. 单次调用信息产出：智谱 sogou 是 Bocha 的 **9.2 倍**
5. ⚠️ 智谱 sogou 召回的 URL 质量需警惕：包含大量企业官网首页 `foyotec.com/`，未必都是文章页
6. 智谱 freshness 完全失效（oneDay/oneWeek/oneMonth/noLimit 召回数全相同）

---

## 数据细节

### count 兑现率（freshness=oneWeek）

| count | 实际召回 | link 完整率 | 延迟 | content p50 |
|-----:|--------:|-----------:|-----:|----------:|
| 10 | 10.0 | 100% | 1080ms | 3101 |
| 30 | 30.0 | 100% | 1274ms | 2972 |
| 50 | 48.7 | 100% | 1766ms | 3212 |

count=50 兑现率 ~97%（部分 query 给 46-50 条），延迟比 count=10 增加 60%，但内容产出 4.4x。

### freshness 完全失效

| freshness | avg_n (count=30) | publish_date 覆盖 |
|----------|-----------------:|-----------------:|
| oneDay | 29.7 | 100% |
| oneWeek | 30.0 | 100% |
| oneMonth | 30.0 | 100% |
| noLimit | 30.0 | 100% |

**结论**：智谱的 `search_recency_filter` 几乎不过滤任何东西。
**意味着**：客户端必须按 `publish_date` 字段硬过滤新鲜度。

### Bocha vs 智谱 URL 重叠率（count=50, oneWeek）

| Query | Bocha 召回 | 智谱召回 | URL 重叠 |
|------|----------:|--------:|--------:|
| 注塑机 新品发布 | 38 | 48 | **0.0%** |
| 高分子材料 研究进展 | 38 | 50 | **0.0%** |
| 塑料污染 治理 政策 | 47 | 46 | **0.0%** |
| polymer industry news | 20 | 50 | **0.0%** |
| biodegradable polymer research | 35 | 48 | **0.0%** |
| plastic recycling regulation | 48 | 50 | **0.0%** |

**域名级 Jaccard 也是 0%**（28 + 46 个域名，零交集）。

#### 各自偏好的内容生态（以"注塑机 新品发布"为例）

**Bocha 偏好**：
- shifair.com（行业展会）
- foodmate.net（食品工业）
- xnnews.com.cn（本地新闻）
- 51sole.com（商品详情）
- finance.jrj.com.cn（金融新闻）

**智谱 sogou 偏好**：
- cnblogs.com（技术博客）
- so.html5.qq.com（QQ 新闻聚合）
- foyotec.com / rijingzsj.com / shiyanshi-zhusuji.com（企业官网）

→ 两个搜索源的内容生态**完全不同**。

### 中英文表现

| 语言 | 召回 (c=30) | content avg | 延迟 |
|------|----------:|-----------:|-----:|
| zh | 30.0 | **8457 字** | 1466ms |
| en | 30.0 | 3576 字 | 1082ms |

中文场景下信息密度比 Bocha 高 ~10 倍。

### 单次调用信息产出对比

| 搜索源 | count=50 单次产出 |
|--------|-----------------:|
| Bocha (summary=true) | ~30K 字（800 字截断 × 38 条） |
| 智谱 sogou (content_size=high) | **~281K 字**（5600 字 × 50 条） |
| **倍数** | **9.2x** |

---

## 关键风险与待验证

### 🚨 风险 1: 智谱 sogou URL 质量参差

抽样发现智谱召回包含：
- 企业官网首页（`foyotec.com/`、`rijingzsj.com/`）— 不是文章页
- 技术博客（cnblogs）— 可能不是新闻
- 落地页 / 营销页

需要在 exp2 里实测：智谱 sogou 拿到的 URL，用 Trafilatura/Jina 抓出来后**有多少是真文章**。

### 🚨 风险 2: 智谱 freshness 不可信

不能依赖 `search_recency_filter=oneWeek` 控制时效。客户端必须按 `publish_date` 字段过滤。

### ✅ 优势 1: 0% 重叠 = 真双源

不是 70% 重叠的"冗余"，是 0% 重叠的"互补"。
意味着 Bocha + 智谱并行可以让候选池**真正翻倍**。

### ✅ 优势 2: content 字数远超 Bocha

如果智谱 sogou content 字段质量足够好（待 exp2 验证），可以直接替代部分 read_page 调用。

---

## 推荐行动（待最终决策）

| 优先级 | 行动 | 理由 |
|-------|------|------|
| P0 | `app/services/zhipu_search.py` 默认引擎已经是 `search_pro_sogou`，仅需校验它真的被触发 | 实验确认 sogou 是唯一可用变体 |
| P0 | 必须传 `content_size=high` | 否则 content 字段缩水 |
| P0 | 客户端按 `publish_date` 硬过滤新鲜度 | freshness 参数对智谱完全失效 |
| P1 | 智谱默认 `count=50`（与 Bocha 对齐） | 兑现率 97%+，延迟仍可接受 |
| P1 | 在 exp2 里实测智谱 URL 的"真文章率" | 决定能否作为主要候选源 |
| P2 | 如果 exp2 证明智谱 URL 质量过关，把 ingester 改成 Bocha+智谱并行 | 0% 重叠 = 双倍互补候选池 |

---

## 数据文件

- `experiments/search_v2/results/exp4_ext_raw.json` — 72 次调用 + 完整结果
- `experiments/search_v2/results/exp4_ext_summary.csv`
- `experiments/search_v2/results/zhipu_link_probe.json` — 三引擎初探数据
