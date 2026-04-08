# Phase 1: 增强日报 Agent — 实施计划

## 背景
当前日报 pipeline 最大失败因子是 `missing_published_at_candidate` 和 `outside_window`，导致 `policy_fill_rate=0.0`、`image_fill_rate=0.0`。Phase 1 目标是让自动发布日报连续多天可演示，达到 2-3 条、2 板块、2+ 图片。

## Proposed Changes

### 1. 政策源专项修复

#### [MODIFY] [pipeline.py](file:///home/wppjkw/workflow_news/app/services/pipeline.py)

**1.1 新增 `_extract_policy_date()` 方法**
- 对政策类高层级域名（`gov.cn`、`miit.gov.cn`、`samr.gov.cn`、`ndrc.gov.cn`、`mee.gov.cn`、地方工信），从页面 markdown/HTML 内容中提取发布日期
- 提取策略：匹配常见中文日期格式（`YYYY年MM月DD日`、`YYYY-MM-DD`、`发布日期：`、`发文日期：`等）
- 仅在 [published_at](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#1279-1291) 为 None 时触发

**1.2 修改 [_prefilter_candidate()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#2626-2676) 放宽政策高层级源拦截**
- 当前逻辑（L2656-2666）：`published_at is None` 且 `source_tier not in ALLOW_MISSING_PUBLISHED_AT_TIERS` 时直接返回 `missing_published_at_candidate`
- 修改：对 `ALLOW_MISSING_PUBLISHED_AT_TIERS` 中的源（government、standards 等），如果 [published_at](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#1279-1291) 为 None，不在 prefilter 阶段拦截，而是标记为 `needs_date_extraction`，允许进入提取阶段

**1.3 修改 [_extract_articles()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#1671-1755) 增加政策日期提取**
- 在 L1828-1831 的 `published_at is None` 拦截之前，先尝试调用 `_extract_policy_date()`
- 如果从页面内容中成功提取了日期，使用该日期继续流程
- 如果提取失败，对 `government/standards` 层级的源允许以"候选解释层"身份进入，标记 `extraction_status="policy_undated"`，但不计入 [complete](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#772-818) 状态

---

### 2. 图片专项修复

#### [MODIFY] [pipeline.py](file:///home/wppjkw/workflow_news/app/services/pipeline.py)

**2.1 拆分图片统计**
- 在 [run()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#339-696) 结尾的 [debug_payload](file:///home/wppjkw/workflow_news/app/services/evaluation.py#87-91) 中新增两个字段：
  - `items_with_unqualified_image`: 条目有图但图片不合格的数量
  - `items_without_any_image`: 条目根本无图的数量
- 当前 `items_without_image` 保留但语义更清晰

**2.2 新增可信相关图补位**
- 在 [_build_image_candidates_for_article()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#2449-2495) 中增加逻辑：当 article 自带的 inline/og 图片全部被拒时，尝试从 article 的 source domain 上搜索一张相关图（仅限 `government`、`top-industry-media`、`academic-journal` 层级）
- 新增 `image_origin_type="trusted_related"` 类型，标上来源可追溯

**2.3 调整完成阈值**
- 将"达到演示可用"的最低图片标准设为 2 而非 3：
  - `MIN_COMPLETE_IMAGES` 维持 3（这是 [complete](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#772-818) 标准）
  - 新增 `MIN_PARTIAL_IMAGES = 2`，用于 [partial](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#972-991) 状态判定
  - [partial](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#972-991) 状态下 2+ 图即可自动发布

---

### 3. Researcher 收缩为主题化检索

#### [MODIFY] [pipeline.py](file:///home/wppjkw/workflow_news/app/services/pipeline.py)

**3.1 明确 query budget**
- `SECTION_QUERY_LIMITS` 已经存在（L94-98），保持不变即可：`industry: 4, policy: 5, academic: 3`
- 在 [_plan_queries()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#711-807) 中增加每个 section 的优先来源簇约束

**3.2 图片任务配额**
- 在 [_research_candidates_by_section()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#1080-1134) 中，为每个 section 设置 image search 配额：
  - `industry`: 2 次图片搜索
  - [policy](file:///home/wppjkw/workflow_news/app/services/pipeline.py#2855-2876): 1 次
  - `academic`: 1 次

#### [MODIFY] [llm.py](file:///home/wppjkw/workflow_news/app/services/llm.py)

**3.3 强化 planner 系统 prompt**
- 明确主题槽位约束、不允许泛化搜索

---

### 4. Supervisor 按缺口驱动

#### [MODIFY] [pipeline.py](file:///home/wppjkw/workflow_news/app/services/pipeline.py)

**4.1 修改 [_supervisor_fallback()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#1433-1467) (L1433-1466)**
- Round 2 只允许三个动作：`retry_for_policy`、`retry_for_images`、`retry_for_quality`（替换弱主条目）
- 不允许泛化 `retry_for_quality` 做全局重搜

**4.2 修改 [_execute_round()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#1135-1351) 中 round 2 的逻辑**
- 当 `supervisor_action == "retry_for_quality"` 时，约束只允许替换 `combined_score < 0.5` 的弱条目
- 不允许增加新的 section 搜索

#### [MODIFY] [llm.py](file:///home/wppjkw/workflow_news/app/services/llm.py)

**4.3 更新 supervisor 系统 prompt (L210-232)**
- 明确 Round 2 只能补缺口，不能重搜

---

### 5. 上线状态语义固定

#### [MODIFY] [pipeline.py](file:///home/wppjkw/workflow_news/app/services/pipeline.py)

**5.1 修改 [_status_for_report_items()](file:///home/wppjkw/workflow_news/app/services/pipeline.py#2757-2766)**
- [complete](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#772-818): 3-5 条 + 2 板块 + 3 图 + 无 fallback
- [partial](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#972-991): 2+ 条 + 允许自动发布 + 附带缺口说明
- `degraded`: 仅 provider/runtime/stage fallback
- `failed`: 没有足够可信内容

**5.2 新增 `_partial_gap_description()` 方法**
- 返回 [partial](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#972-991) 状态下缺失的是条目、板块还是图片

**5.3 在 Report 模型中增加 `gap_description` 属性或在 debug_payload 中记录**

---

## Verification Plan

### Automated Tests
运行现有全套测试确保不回归：
```bash
cd /home/wppjkw/workflow_news && python -m pytest tests/test_native_pipeline.py -v
```

新增以下测试用例（在 [test_native_pipeline.py](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py) 中）：

1. **`test_policy_date_extraction_from_content`** — 验证从 markdown 内容中可以提取中文政策日期
2. **`test_policy_source_not_rejected_when_undated`** — 验证政策高层级源不会因缺日期而在 prefilter 阶段被拦截
3. **`test_image_stats_split`** — 验证 `items_with_unqualified_image` 和 `items_without_any_image` 分别计数
4. **`test_partial_status_with_two_images`** — 验证 2 张图时状态为 [partial](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#972-991)，3 张图时为 [complete](file:///home/wppjkw/workflow_news/tests/test_native_pipeline.py#772-818)
5. **`test_supervisor_round2_only_fills_gaps`** — 验证 round 2 不扩大搜索范围
6. **`test_status_semantics`** — 验证 `complete/partial/degraded/failed` 状态语义

### Manual Verification
单次手动 run 验证改善效果（需要有效的 API key 配置）：
```bash
cd /home/wppjkw/workflow_news && python -c "
import asyncio
from app.database import session_scope
from app.services.pipeline import NativeReportPipeline
async def main():
    pipeline = NativeReportPipeline()
    with session_scope() as session:
        report = await pipeline.run(session, shadow_mode=False)
        print(f'Status: {report.status}')
        print(f'Items: {len(report.items)}')
        print(f'Sections: {set(item.section for item in report.items)}')
        print(f'Images: {sum(1 for item in report.items if item.has_verified_image)}')
asyncio.run(main())
"
```
