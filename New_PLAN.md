╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
 Agent 平台全面改进方案                                                                                                                                         
                                                                                                                                                                
 Context                                                                                                                                                     

 这是一个高分子材料加工领域的每日资讯 Agent 平台，已经从确定性 Pipeline 迁移到 LLM 驱动的 Agent Loop 架构。核心设计理念正确（Harness 约束边界、Agent
 自主决策），但在实际运行中存在多个影响可靠性的问题：工具调用无超时保护、消息历史无限增长、finish 拒绝可能导致死循环、异常处理粗糙、外部 API
 无熔断机制等。需要系统性地修复这些问题，使其成为一个真正能稳定产出日报的 Agent 平台。

 ---
 Phase 1: Agent 核心可靠性（最高优先级）

 1.1 工具执行超时保护

 - 文件: app/services/agent_core.py:204-212, app/services/harness.py
 - 问题: await tool.execute(...) 无 asyncio.wait_for，单个工具挂死则整个循环阻塞
 - 修改:
   - Harness 新增 tool_timeouts: dict[str, float] 字段，默认值:
       - web_search: 30s, read_page: 25s, evaluate_article: 15s, write_section: 20s
     - search_images: 20s, verify_image: 10s, compare_sources: 15s
     - follow_references/check_coverage/finish: 5s
   - Harness 新增 tool_timeout(tool_name) -> float 方法
   - AgentCore 的工具执行改为:
   timeout = self.harness.tool_timeout(tool_call.tool_name)
 tool_result = await asyncio.wait_for(tool.execute(...), timeout=timeout)
   - 捕获 asyncio.TimeoutError 作为独立异常类型

 1.2 消息历史管理（防 Context Window 溢出）

 - 文件: app/services/agent_core.py:106-252
 - 问题: messages 列表每步追加，100步后可能超过模型 128k 上下文
 - 修改:
   - 新增 _trim_messages(messages, keep_recent=15, max_total_chars=80000) 方法
   - 保留: system message (index 0) + task message (index 1) + 最近 15 条完整消息
   - 中间消息: 工具结果压缩为 "[{tool_name}: {summary前100字}...]"
   - 在每次 _get_llm_decision 调用前触发裁剪
   - 复用已有的 memory.to_context_summary() 作为压缩后的上下文补充

 1.3 修复 Finish 拒绝死循环

 - 文件: app/services/agent_core.py:315-328, app/services/harness.py
 - 问题: LLM 反复调用 finish 但不满足 min_searches=6/min_articles=4，StopIteration 后 LLM 再次尝试，可能循环数十次
 - 修改:
   - AgentCore.run() 新增 consecutive_finish_rejects = 0 计数器
   - 每次 StopIteration 后递增，非 finish 的工具调用时重置为 0
   - 超过 3 次连续拒绝时，强制接受 finish（Agent 明显已陷入困境）
   - Harness 新增可配置字段: min_searches_before_finish=6, min_articles_before_finish=4
   - 替代 agent_core.py 中的 getattr(self.harness, ...) 硬编码

 1.4 分类异常处理

 - 文件: app/services/agent_core.py:206-212
 - 问题: 裸 except Exception 将所有错误统一为 ToolResult(success=False)，无法区分网络超时、限流、解析错误
 - 修改:
 except asyncio.TimeoutError:
     tool_result = ToolResult(success=False, summary=f"工具超时({tool_call.tool_name})",
                             data={"error_type": "timeout"})
 except httpx.HTTPStatusError as exc:
     error_type = "rate_limit" if exc.response.status_code == 429 else "http_error"
     tool_result = ToolResult(success=False, summary=f"HTTP {exc.response.status_code}",
                             data={"error_type": error_type})
 except httpx.TimeoutException:
     tool_result = ToolResult(success=False, summary="网络超时",
                             data={"error_type": "network_timeout"})
 except json.JSONDecodeError:
     tool_result = ToolResult(success=False, summary="返回格式错误",
                             data={"error_type": "parse_error"})
 except Exception as exc:
     logger.error("Unexpected tool error: %s", exc, exc_info=True)
     tool_result = ToolResult(success=False, summary=f"异常: {exc}",
                             data={"error_type": "unexpected"})

 1.5 动态预算感知（防超时浪费）

 - 文件: app/services/harness.py
 - 问题: 固定 max_steps 不考虑剩余时间，可能在超时前浪费步骤
 - 修改:
   - 新增 effective_budget_remaining 属性，取 min(step_budget, time_based_budget)
   - 新增 should_wind_down 属性 (剩余预算 < 5 步或剩余时间 < 60秒)
   - AgentCore 在 should_wind_down 时注入提示: "资源即将耗尽，请尽快使用 write_section 撰写内容并调用 finish。"
   - AgentCore 主循环改用 effective_budget_remaining 替代 budget_remaining

 ---
 Phase 2: 提示词工程 & 工具调用策略

 2.0 重写 DailyReportAgent System Prompt

 - 文件: app/services/daily_report_agent.py:17-26
 - 当前问题:
   - System prompt 只有4条笼统的"工作指南"，缺乏具体的工具使用策略
   - Task prompt (_build_task_prompt) 把策略指导塞在 user message 里，与 system role 混淆
   - 没有告诉 Agent 工具的调用顺序和组合模式
   - 没有阶段性节奏感——Agent 不知道什么时候该转入收尾
 - 修改: 重写为三层结构的 system prompt:

 【角色定位】
 你是高分子材料加工领域的专业情报分析 Agent。你的任务是通过自主搜索、阅读、评估，
 生成一份高质量的每日行业资讯日报。

 【工作节奏】（核心新增）
 你的工作分为三个阶段，请按节奏推进：

 阶段一 · 广度搜索（前 40% 的步骤预算）
 - 执行 6-8 轮 web_search，覆盖三个维度：
   · 产业动态：注塑/挤出/吹塑设备、原料价格、企业扩产
   · 技术前沿：高分子改性/回收/生物基、新材料研究
   · 政策标准：环保法规、行业标准、碳关税
 - 中英文交替搜索，每个维度至少 2 次
 - 搜索词要具体（如"注塑机 新品发布"而非泛泛的"高分子"）
 - 每次搜索后快速浏览结果，选择 2-3 条有价值的用 read_page 深入

 阶段二 · 深度评估（中间 40% 的步骤预算）
 - 对已阅读的文章逐一调用 evaluate_article
 - 调用 check_coverage 检查三个板块的覆盖情况
 - 如果某个板块不足，针对性补搜
 - 用 compare_sources 去重，确保内容不重叠
 - 为有价值文章用 search_images 找配图，用 verify_image 验证

 阶段三 · 撰写收尾（最后 20% 的步骤预算）
 - 最后一次 check_coverage 确认状态
 - 对每个有文章的板块调用 write_section
 - 调用 finish 输出最终报告

 【质量标准】
 - 目标：4-8 篇文章，覆盖至少 2 个板块，2+ 张验证配图
 - 每篇文章必须有：中文标题、来源引用、核心发现（💡科研雷达）
 - 优先级：大陆权威媒体 > 英文学术/产业 > 其他
 - 严格过滤：72小时内内容，不收录旧闻

 【工具使用注意事项】
 - web_search: 不要重复相同的搜索词，换角度搜索
 - read_page: 阅读后立即 evaluate_article，不要积攒
 - evaluate_article: 只传入已阅读的内容，不要凭搜索摘要评估
 - check_coverage: 每收集 3-4 篇文章后调用一次
 - write_section: 先确认板块有 1+ 篇文章再写
 - finish: 必须在所有 write_section 完成后调用

 2.0b 改进 _build_system_message

 - 文件: app/services/agent_core.py:261-270
 - 当前问题: 只输出工具名字列表和简略的数字限制
 - 修改:
   - 输出每个工具的 name + 一句话说明（从 tool.description 截取首句）
   - 输出 harness 的完整预算信息（步骤/搜索/阅读/时间 + 剩余量）
   - 当 harness.should_wind_down 时追加收尾提醒

 2.0c 改进 Task Prompt

 - 文件: app/services/daily_report_agent.py:176-194
 - 当前问题: task prompt 里混入了大量策略指导（应该在 system prompt 里）
 - 修改:
   - Task prompt 精简为纯任务描述:
   请生成今日《高分子加工全视界日报》。
 当前时间：{now}
 时效要求：只收录过去 72 小时内的内容。
   - 所有策略指导移入 system prompt（Phase 2.0 已覆盖）

 2.0d 工具 Description 优化

 - 文件: app/services/tools.py (各工具类的 description 字段)
 - 当前问题: 部分工具描述过长或不清晰，LLM 可能不理解最佳使用时机
 - 修改:
   - web_search: 补充"搜索后用 read_page 深入有价值的结果"
   - read_page: 补充"阅读后请立即用 evaluate_article 评估"
   - follow_references: 补充"只在需要追踪引用来源时使用，普通文章不需要"
   - evaluate_article: 强调"只评估已用 read_page 阅读过的文章"
   - check_coverage: 补充"每收集 3-4 篇文章后调用"
   - write_section: 补充"先用 check_coverage 确认板块有足够文章"
   - finish: 补充"必须在所有需要的 write_section 完成后调用"

 2.0e 工作记忆上下文摘要优化

 - 文件: app/services/working_memory.py:352-388 (to_context_summary)
 - 当前问题: 摘要缺少阶段感知和下一步建议
 - 修改:
   - 新增阶段判断逻辑:
       - 搜索次数 < 6 → "当前处于广度搜索阶段"
     - 搜索次数 >= 6 且文章数 < 4 → "当前处于深度评估阶段"
     - 文章数 >= 4 → "当前处于撰写收尾阶段"
   - 每次摘要末尾追加一句建议（基于当前状态和缺口）
   - 展示已写板块和未写板块的状态

 ---
 Phase 3: 工具实现质量改进

 3.1 统一域名过滤配置

 - 文件: app/services/tools.py:88-103, app/services/harness.py:22-38
 - 问题: 两处独立的硬编码域名黑名单 + 粗暴的 .com.tw 全 TLD 封禁
 - 修改:
   - 合并 _BLOCKED_RESULT_DOMAINS (tools.py) 和 DEFAULT_BLOCKED_DOMAINS (harness.py) 为单一来源
   - 移除 .com.tw/.org.tw 全 TLD 封禁，改为显式域名列表
   - 从 AppSetting 表加载（key: agent.blocked_domains），支持运行时更新
   - Harness 构造时从 DB 读取配置，Tools 通过 Harness 引用获取黑名单
   - 72小时新鲜度阈值也提取为 AppSetting（key: agent.freshness_hours），默认 72

 3.2 内容提取优化

 - 文件: app/services/tools.py:328 (ReadPageTool)
 - 问题: 硬截断 markdown[:6000]，长文章丢失后半部分的关键发现
 - 修改:
   - 改为分段提取: 前 2500字 (引言) + 后 1500字 (结论) + 中间关键词密度最高的 2000字
   - 总上限调整为 8000 字（对比当前 content_summary 基于 6000 字，data.markdown 基于 8000 字）
   - 复用已有的 summarize_markdown() 函数和 DEFAULT_DOMAIN_KEYWORDS 做关键词密度计算

 3.3 评估工具启发式预过滤

 - 文件: app/services/tools.py:480- (EvaluateArticleTool)
 - 问题: 每篇文章都调 LLM 评估，即使明显不相关的也浪费 LLM 配额
 - 修改:
   - 在 LLM 调用前新增启发式检查:
       - 统计 domain_keywords 在 title+content 中的命中数
     - 命中数 = 0: 直接拒绝，不调 LLM（返回 worthy=false, reason="无领域关键词"）
     - 命中数 >= 3: 简化 LLM prompt（只需分类 section + 提取 key_finding）
     - 命中数 1-2: 完整 LLM 评估
   - 预计减少 30-40% 的评估 LLM 调用

 3.4 搜索重试失败快速降级

 - 文件: app/services/tools.py:166-191 (WebSearchTool), app/services/brave.py, app/services/firecrawl.py
 - 问题: 外部 API 挂掉时每次工具调用都重试，浪费步骤
 - 修改:
   - 在 BraveSearchClient 和 FirecrawlClient 中添加简单熔断器:
   class CircuitBreaker:
     failure_threshold: int = 3
     reset_timeout: float = 60.0
   - 连续 3 次失败后进入 open 状态，60秒内直接返回错误不发请求
   - 60秒后进入 half-open 状态，尝试一次请求，成功则恢复
   - WebSearchTool 在熔断器 open 时返回明确提示: "搜索服务暂时不可用，请尝试其他工具"

 ---
 Phase 4: Multi-Agent 架构（核心升级）

 架构总览

 DailyReportAgent.run() — 三阶段编排器
 │
 ├─ Phase 1: 广度搜索（AgentCore + 受限工具集）
 │   LLM 自主决策搜索方向，只提供 web_search + check_coverage + finish
 │   搜索结果存入共享 WorkingMemory.search_results
 │
 ├─ Phase 2: 并发文章处理（N 个 ArticleAgent）
 │   代码从 memory.search_results 提取候选 URL
 │   asyncio.gather 并发 spawn ArticleAgent（上限 5 个）
 │   每个 ArticleAgent 执行固定流水线：read → evaluate → find_image → verify
 │   结果通过共享 WorkingMemory 汇聚
 │
 └─ Phase 3: 编排综合（AgentCore + 综合工具集）
 │   LLM 用 compare_sources 去重 → write_section 撰写 → finish 输出
 │
 └─ Fallback: 全部 Article Agent 失败 → 回退到单体 AgentCore

 4.1 新文件: app/services/article_agent.py（~150行）

 ArticleCard 数据结构（sub-agent 的输出）:
 @dataclass
 class ArticleCard:
     url: str
     title: str
     domain: str
     source_name: str
     published_at: str | None
     summary: str
     section: str              # academic / industry / policy / rejected
     key_finding: str
     image_url: str | None
     image_caption: str | None
     evaluation_reason: str
     success: bool
     error: str | None = None
     steps_used: int = 0
     tokens_used: int = 0

 ArticleHarness（轻量预算）:
 @dataclass
 class ArticleHarness:
     max_steps: int = 8
     max_reads: int = 1
     max_llm_calls: int = 3    # evaluate + verify + retry
     max_duration_seconds: float = 60.0

 ArticleAgent — 固定流水线，不是 LLM 循环:
 - 关键设计决策：3-4 步是确定性的，用 LLM 循环浪费 token 且增加延迟
 - 接收: url + context + 共享 WorkingMemory + 共享 Tools
 - 流程:
   a. read_page(url) → 获取全文内容
   b. evaluate_article(title, content, url) → 评估价值，分类板块
   c. 如果 image_worthiness=true → 先检查 read_page 内联图，否则 search_images
   d. 如果找到图 → verify_image 验证
   e. 返回 ArticleCard
 - 任一步骤失败 → 返回 ArticleCard(success=False, error=...)
 - 通过共享 tool 实例操作共享 memory（read_page 写 record_read，evaluate 写 add_article）

 4.2 改造: app/services/agent_core.py

 最小改动——接受外部 WorkingMemory:
 - run() 方法新增可选参数 memory: WorkingMemory | None = None
 - 如果传入则使用传入的，否则创建新的（保持向后兼容）
 - AgentResult 新增 _memory 字段（内部引用，不序列化），方便编排器读取完整状态

 4.3 改造: app/services/working_memory.py

 新增字段:
 self.search_results: list[dict[str, Any]] = []  # 原始搜索结果存储
 self._lock: asyncio.Lock = asyncio.Lock()        # 并发安全锁

 为什么不需要大规模加锁: asyncio 是单线程协作式多任务。只在 await 点切换。WorkingMemory 的写操作（record_read, add_article 等）都是纯内存操作，没有
 await。且每个 ArticleAgent 处理不同的 URL，不会竞争同一资源。Lock 只是防御性保护。

 4.4 改造: app/services/tools.py

 WebSearchTool.execute() 新增结果存储:
 # 在返回前，将原始结果写入 memory
 for r in results[:10]:
     memory.search_results.append({
         "url": r.get("url", ""), "title": r.get("title", ""),
         "domain": r.get("domain", ""), "snippet": r.get("snippet", ""),
         "published_at": r.get("published_at"),
     })

 4.5 重写: app/services/daily_report_agent.py

 三阶段编排流程:

 Phase 1 — 广度搜索:
 - 工具集: [WebSearchTool, CheckCoverageTool, FinishTool]（无 read_page）
 - Harness: max_steps=20, max_search_calls=12, max_page_reads=0, max_duration=300s
 - System prompt: "你是搜索专家，只需要搜索发现文章链接，不需要阅读"
 - 搜索完成后 memory.search_results 中有所有候选 URL

 Phase 2 — 并发文章处理:
 - _extract_candidate_urls(memory): 从 search_results 提取去重后的 top 8-12 个 URL
 - asyncio.gather + asyncio.Semaphore(5) 并发运行 ArticleAgent
 - 每个 ArticleAgent 有 60s 超时保护 (asyncio.wait_for)
 - 超时/异常的 Agent 返回 ArticleCard(success=False) 而非崩溃
 - 所有 Agent 完成后检查成功数量

 Phase 3 — 编排综合:
 - 工具集: [CompareSourcesTool, WriteSectionTool, CheckCoverageTool, FinishTool]（无搜索/阅读）
 - Harness: max_steps=15, max_search_calls=0, max_page_reads=0, max_duration=300s
 - System prompt: "你是编辑，文章已就绪，请去重、撰写板块内容、完成日报"

 Fallback:
 - 如果 Phase 2 成功的 ArticleCard 数量为 0
 - 回退到原来的单体 AgentCore 运行（全工具集 + 100步预算）
 - 保证服务可用性

 4.6 Phase 分阶段 System Prompt

 搜索阶段 prompt:
 你是高分子材料加工领域的情报搜索专家。
 你的唯一任务是发现有价值的文章链接——不需要阅读、不需要写报告。

 工作要求：
 1. 执行至少 6 轮 web_search，覆盖产业/技术/政策三个维度
 2. 中英文交替搜索，搜索词要具体（"注塑机 新品发布"而非"高分子"）
 3. 每轮搜索后用 check_coverage 评估进度
 4. 搜索充分后调用 finish 结束搜索阶段

 综合阶段 prompt:
 你是高分子材料加工日报的总编辑。
 前序 Agent 已经完成了文章的搜索、阅读和评估，现在你需要：
 1. 调用 compare_sources 对比去重
 2. 调用 check_coverage 确认最终状态
 3. 为每个有文章的板块调用 write_section 撰写内容
 4. 最后调用 finish 输出完整日报

 当前已有文章和配图状态会在工作记忆中展示。

 4.7 ReportItem 数据保真度（同步改进）

 - 文件: app/services/daily_report_agent.py:124-151
 - research_signal 使用 article 的 key_finding 而非硬编码 "基于 Agent 生成"
 - source_name 优先使用 article.source_name（如果有）
 - combined_score 使用 article 评估分数

 ---
 Phase 5: 可观测性增强

 5.1 AgentStep 错误分类持久化

 - 文件: app/models.py (AgentStep), app/services/agent_core.py:402-428
 - 修改:
   - AgentStep 新增 error_category 列 (nullable String(32))
   - 值域: timeout | http_error | rate_limit | parse_error | harness_block | circuit_breaker | unexpected | null
   - _persist_step 接受新参数 error_category，写入 AgentStep
   - 这样可以查询: "过去7天有多少超时错误 vs 限流错误"

 5.2 评估打分权重文档化

 - 文件: app/services/evaluation.py
 - 修改:
   - 提取所有魔法数字为模块顶部的命名常量并加注释
   - 如 CONTENT_WEIGHT = 0.35  # 内容完整度在总分中的权重
   - 可选: 从 AppSetting 读取权重，允许运行时微调

 ---
 实施顺序

 ┌────────┬─────────────────────────────────┬──────┬──────────────────────────────────────────┐
 │ 优先级 │              任务               │ 风险 │                 主要文件                 │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P0     │ 1.1 工具超时保护                │ 低   │ agent_core.py + harness.py               │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P0     │ 1.3 finish 死循环修复           │ 低   │ agent_core.py + harness.py               │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P0     │ 1.4 分类异常处理                │ 低   │ agent_core.py                            │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 1.2 消息历史管理                │ 中   │ agent_core.py                            │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 1.5 动态预算感知                │ 低   │ harness.py + agent_core.py               │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 2.0 重写 System Prompt          │ 低   │ daily_report_agent.py                    │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 2.0b 改进 _build_system_message │ 低   │ agent_core.py                            │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 2.0c 精简 Task Prompt           │ 低   │ daily_report_agent.py                    │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 2.0d 工具 Description 优化      │ 低   │ tools.py                                 │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 2.0e 工作记忆摘要优化           │ 低   │ working_memory.py                        │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P1     │ 3.4 熔断器                      │ 低   │ brave.py + firecrawl.py                  │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 3.1 域名过滤配置化              │ 低   │ tools.py + harness.py                    │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 3.3 评估预过滤                  │ 低   │ tools.py                                 │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 3.2 内容提取优化                │ 低   │ tools.py                                 │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 4.1 新建 ArticleAgent           │ 中   │ article_agent.py（新文件）               │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 4.2 AgentCore 支持外部 Memory   │ 低   │ agent_core.py                            │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 4.3 WorkingMemory 扩展          │ 低   │ working_memory.py + tools.py             │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 4.4 WebSearchTool 结果存储      │ 低   │ tools.py                                 │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 4.5 DailyReportAgent 三阶段重写 │ 高   │ daily_report_agent.py                    │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P2     │ 4.6 分阶段 System Prompt        │ 低   │ daily_report_agent.py                    │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P3     │ 5.1 错误分类持久化              │ 低   │ models.py + agent_core.py + bootstrap.py │
 ├────────┼─────────────────────────────────┼──────┼──────────────────────────────────────────┤
 │ P3     │ 5.2 评估权重文档化              │ 低   │ evaluation.py                            │
 └────────┴─────────────────────────────────┴──────┴──────────────────────────────────────────┘

 建议实施路径: Phase 1 先行（修复可靠性）→ Phase 2 提示词（立即可见效果）→ Phase 3 工具质量 → Phase 4 multi-agent（核心升级，需前三个 Phase 的基础）→ Phase 5
 可观测性

 ---
 关键文件清单

 ┌────────────────────────────────────┬───────────────────────────────────────────────────────────┐
 │                文件                │                         改动类型                          │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/agent_core.py         │ Phase 1 全部 + 2.0b + 4.2（接受外部 memory）+ 5.1         │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/harness.py            │ 1.1 + 1.3 + 1.5 + 3.1 + 4.5（分阶段 harness preset）      │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/tools.py              │ 2.0d + 3.1~3.4 + 4.4（search_results 存储）               │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/daily_report_agent.py │ 2.0 + 2.0c + 4.5（三阶段重写）+ 4.6 + 4.7                 │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/working_memory.py     │ 2.0e + 4.3（search_results + asyncio.Lock）               │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/article_agent.py      │ 4.1 新文件（ArticleCard + ArticleHarness + ArticleAgent） │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/llm_client.py         │ 小改（配合 1.4 错误类型）                                 │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/brave.py              │ 3.4 熔断器                                                │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/firecrawl.py          │ 3.4 熔断器                                                │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/services/evaluation.py         │ 5.2                                                       │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/models.py                      │ 5.1 新列                                                  │
 ├────────────────────────────────────┼───────────────────────────────────────────────────────────┤
 │ app/bootstrap.py                   │ 5.1 migration                                             │
 └────────────────────────────────────┴───────────────────────────────────────────────────────────┘

 ---
 验证方案

 1. 单元测试 (扩展 tests/test_agent_core.py):
   - 工具超时: mock 一个 sleep(60s) 的工具，验证 TimeoutError 被正确捕获
   - finish 循环: mock LLM 始终返回 finish，验证 3 次后强制接受
   - 消息裁剪: 构造 50 步的消息历史，验证裁剪后 < max_total_chars
   - 熔断器: mock 连续 3 次失败，验证第 4 次直接返回不发请求
 2. ArticleAgent 测试 (新建 tests/test_article_agent.py):
   - mock read_page/evaluate/search_images/verify_image
   - 验证成功路径返回完整 ArticleCard
   - 验证 read_page 失败时优雅降级
   - 验证 60s 超时保护生效
   - 验证共享 memory 被正确写入（record_read, add_article 等）
 3. Multi-Agent 集成测试 (新建 tests/test_multi_agent.py):
   - mock Brave/Firecrawl 返回固定搜索结果
   - 验证三阶段完整流程: 搜索→并发处理→综合
   - 验证 5 个 ArticleAgent 并发运行时 memory 状态一致
   - 验证全部 Agent 失败时回退到单体模式
   - 验证部分 Agent 超时时其他 Agent 正常完成
 4. 手动验证:
   - 运行 POST /api/reports/run 触发日报生成
   - 检查 /api/agent-runs/{id}/trace 步骤记录中能区分 Phase 1/2/3
   - 对比 multi-agent 和单体模式的生成时间和文章质量
   - 验证并发搜索图片不会相互干扰