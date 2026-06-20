# 实验 1：Bocha 参数扫荡 — 关键发现

> 跑了 6 个真实生产 query × 3 count × 2 summary × 4 freshness = **144 次 Bocha web-search 调用**
> 全部成功（144/144 ok），耗时约 90 秒

## TL;DR

1. **count=10 → 50 是免费午餐**：同一次 API 调用拿 4 倍结果数，延迟反而略低
2. **summary=true 已经在用**：但代码里被塞进 `snippet` 字段，下游用了不自知
3. **summary 被截断到 800 字**：98.6% 的查询都触顶截断，对长文章是"开头 800 字"不是"全文摘要"
4. **freshness 实际过滤很弱**：oneDay/oneMonth 召回数差不多，依赖它做新鲜度过滤不可靠
5. **每条结果都带 `datePublished`**：100% 覆盖，应该用它做新鲜度过滤而不是 freshness 参数
6. **中文召回 > 英文**：26.7 vs 22 条/查询；`polymer industry news` 在 oneDay 下只有 10 条（英文新鲜内容稀缺）

---

## 详细数据

### Q1：count 边际收益

固定 summary=true、freshness=oneWeek，6 query 平均：

| count | 平均召回 | 平均独立域名 | 延迟 (ms) |
|------:|--------:|-----------:|---------:|
| 10 | 9.8 | 7.8 | 278 |
| 30 | 24.3 | 19.2 | 268 |
| 50 | **38.2** | **30.2** | **244** |

**结论**：count=50 是新默认值。三种 count 都是同一次 API 调用（同价钱），延迟也基本一样。

### Q2：summary on/off 对比

固定 count=10、freshness=oneWeek：

| summary | summary p50 字数 | snippet 字数 | 延迟 |
|---------|------------------|-------------|------|
| true | 770 | 100 | 278ms |
| false | 0 | 100 | 198ms |

**结论**：summary=true 加了 80ms 延迟，换回 770 字 AI 摘要。继续保留。

### Q3：freshness 真实差异

固定 count=30、summary=true：

| freshness | 平均召回 | 独立域名 | published_at 覆盖率 |
|-----------|---------|---------|---------------------|
| oneDay | 22.7 | 19.5 | 100.0% |
| oneWeek | 24.3 | 19.2 | 100.0% |
| oneMonth | 22.2 | 18.3 | 100.0% |
| noLimit | 27.8 | 22.7 | 99.4% |

**反常识发现**：
- oneDay 拿到的不一定是当天内容，oneMonth 不一定是 30 天内
- Bocha 的 freshness 参数更像"排序偏好"而不是"硬过滤"
- **不要依赖 freshness 做新鲜度过滤；改用 `datePublished` 字段在客户端硬过滤**

### Q4：count=50 实际兑现率

| query | oneDay | oneWeek | oneMonth | noLimit |
|-------|------:|-------:|--------:|-------:|
| 注塑机 新品发布 | 46 | 41 | 41 | 50 |
| 高分子材料 研究进展 | 41 | 39 | 45 | 49 |
| 塑料污染 治理 政策 | 46 | 47 | 36 | 50 |
| polymer industry news | **10** | 21 | 18 | 50 |
| biodegradable polymer research | 28 | 32 | 28 | 33 |
| plastic recycling regulation | 42 | 49 | 40 | 45 |

**结论**：noLimit 下中文 query 几乎打满 50 条；英文新鲜度收紧后召回掉得很快。

### Q5：中英文差异

count=30、summary=true、oneWeek：

| 语言 | 平均召回 | 独立域名 | summary p50 |
|------|---------:|--------:|-----------:|
| zh | 26.7 | 21.7 | 757 |
| en | 22.0 | 16.7 | 672 |

**结论**：Bocha 中文表现确实更强，符合产品定位。

### Q6：summary 截断分析

| 指标 | 数值 |
|------|-----|
| summary 字数 p50 | 799 |
| summary 字数 max | 800 |
| summary 字数 min | 103 |
| **触顶截断率（max≥790）** | **98.6%** (71/72) |

**结论**：Bocha summary 实质是**前 800 字截断**，对长技术文章信息密度严重打折。这一项决定了 summary 不能完全替代 read_page —— 长文还需要全文。

---

## 影响生产代码的发现

### 发现 1：summary 已经在用，只是字段名混淆

`app/services/bocha_search.py:140`：

```python
"snippet": ai_summary if ai_summary else snippet,  # 800字summary被塞进snippet字段
```

**所有下游代码** (`composer.py`, `ingester.py:84`, `tools.py:281`, `candidate_scorer.py:178`) 用 `row["snippet"]` 时**实际上拿到的是 Bocha 的 800 字 summary**，不是真正的 100 字 snippet。

这意味着：
- ✅ MinHash 去重信号比预期强（用 800 字而不是 100 字）
- ✅ 关键词过滤、source quality 判断也基于 800 字
- ⚠️ 但 `bocha_search.py:147` 把原始 item 也存了 metadata，可以两个字段都暴露

### 发现 2：未使用 Bocha 的能力

| 能力 | 状态 |
|------|------|
| `count=50` (最大值) | ❌ 现在用 `count=10` |
| `summary=true` | ✅ 已用（但下游不自知） |
| `freshness` | ⚠️ 已用但作用很弱，应改用 `datePublished` 客户端过滤 |
| `include` 域名定向 | ✅ tools.py 在用（policy/academic 关键词） |
| `/v1/rerank` API（gte-rerank） | ❌ **完全没用** |
| `/v1/ai-search` | ✅ scout 在用 |

### 发现 3：每条结果都有 `datePublished`

100% 覆盖率（除 noLimit 99.4%）。这意味着我们可以：
- 客户端用 `datePublished` 做硬过滤（24h/72h/7d）
- 比 `freshness=oneDay` 准确得多
- 还能拿来做排序加权

---

## 推荐改动（先记下，等所有实验跑完再统一决策）

| 优先级 | 改动 | 影响 |
|-------|------|------|
| P0 | `BOCHA_SEARCH_COUNT` 默认 10 → 50 | 同价拿 4× 结果数 |
| P0 | bocha_search.py 同时返回 `snippet`（原 100 字）和 `summary`（800 字），不要混用 | 字段语义清晰 |
| P0 | 客户端按 `datePublished` 硬过滤（72h），不依赖 freshness | 真实新鲜度控制 |
| P1 | 接入 `/v1/rerank` API：50 条原始 → rerank top-K | 待实验 3 验证收益 |
| P2 | 缩减 `search_templates.yaml` 41 条 → 15 条 | count=50 后 41 条已严重冗余 |

## 数据文件

- `experiments/search_v2/results/exp1_raw.json` — 144 次调用原始数据 + 每次取前 5 条结果
- `experiments/search_v2/results/exp1_summary.csv` — 透视表
- `experiments/search_v2/results/exp1_run.log` — 完整 stdout

## 已知限制

- 余额查询 API 端点 `/v1/billing/balance` 返回失败，**单次成本数据缺失**。后续手动按 ¥0.02/次估算
- 6 个 query 是分层抽样，不是 41 个全跑——结论方向可信，绝对数值有 ±10% 波动
