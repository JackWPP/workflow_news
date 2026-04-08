export interface Citation {
  label?: string
  url: string
}

export interface ReportItem {
  id: number
  section: string
  rank: number
  title: string
  source_name: string
  source_url: string
  published_at: string | null
  summary: string
  research_signal: string
  image_url: string | null
  image_source_url: string | null
  image_origin_type: string | null
  image_caption: string | null
  image_relevance_score: number
  has_verified_image: boolean
  visual_verdict: string | null
  context_verdict: string | null
  visual_score: number
  context_score: number
  final_image_score: number
  selected_for_publish: boolean
  image_reason: string | null
  window_bucket: string
  citations: Citation[]
  combined_score: number
}

export interface Report {
  id: number
  report_date: string
  status: string
  title: string
  markdown_content: string
  summary: string | null
  pipeline_version: string
  debug_url: string | null
  error_message: string | null
  publish_grade: string
  round_count: number
  supervisor_actions: Array<Record<string, unknown>>
  hero_image: Record<string, unknown> | null
  image_review_summary: Record<string, unknown>
  created_at: string
  items: ReportItem[]
}

export interface SourceRule {
  id?: number
  name: string
  domain: string
  type: string
  priority: number
  tags: string[]
  include_rules: string[]
  exclude_rules: string[]
  must_include_any: string[]
  must_exclude_any: string[]
  soft_signals: string[]
  source_tier: string
  rss_or_listing_url: string | null
  crawl_mode: string
  use_direct_source: boolean
  allow_images: boolean
  language: string | null
  country: string | null
  enabled: boolean
}

export interface RetrievalRun {
  id: number
  run_date: string
  started_at: string
  finished_at: string | null
  status: string
  shadow_mode: boolean
  query_count: number
  candidate_count: number
  extracted_count: number
  error_message: string | null
  debug_payload: Record<string, unknown> | null
}

export interface RetrievalQuery {
  id: number
  section: string
  language: string
  query_text: string
  target_type: string
  response_status: string
  result_count: number
  filters: Record<string, unknown> | null
}

export interface RetrievalCandidate {
  id: number
  query_id: number | null
  url: string
  title: string
  domain: string
  section: string
  language: string
  source_type: string
  source_name: string | null
  status: string
  rejection_reason: string | null
  image_url: string | null
  published_at: string | null
  metadata_json: Record<string, unknown> | null
}

export interface ReportSettings {
  report_hour: number
  report_minute: number
  shadow_mode: boolean
  scrape_timeout_seconds: number
  scrape_concurrency: number
  max_extractions_per_run: number
  report_primary_model: string
  report_fallback_model: string
}

export interface QualityFeedback {
  id: number
  target_type: string
  target_id: number
  target_domain: string | null
  target_title: string | null
  label: string
  reason: string | null
  note: string | null
  created_by: number
  created_at: string
}

export interface QualityDomainStats {
  domain: string
  bad_off_topic: number
  bad_pr_like: number
  bad_source: number
  good: number
  keep_borderline: number
}

export interface QualityOverview {
  recent_feedback: QualityFeedback[]
  flagged_domains: QualityDomainStats[]
  hard_rejects: Array<{ reason: string; count: number }>
  daily_quality: Array<{ date: string; status: string; selected_count: number; section_coverage: number }>
  feedback_summary: Record<string, number>
  duplicate_trend: Array<{ run_id: number; date: string; duplicate_ratio: number }>
  source_rule_hotspots: Array<{ domain: string; count: number }>
  high_tier_false_rejects: Array<{ domain: string; count: number }>
  top_policy_misses: Array<{ reason: string; count: number }>
  extended_window_usage: Array<{ date: string; extended_window_selected: number }>
  dominant_domain_runs: Array<{ domain: string; count: number }>
  image_coverage_rate: number
  no_image_rejections: number
  duplicate_image_hits: number
  round2_trigger_rate?: number
  publish_grade_breakdown?: Record<string, number>
  image_review_rejections?: Record<string, number>
  policy_gap_breakdown?: Record<string, number>
  report_score_trend?: Array<{ run_id: number; date: string; score: number }>
  benchmark_score_trend?: Array<{ date: string; score: number }>
  policy_fill_rate?: number
  image_fill_rate?: number
  round2_recovery_rate?: number
  off_topic_escape_count?: number
  average_daily_report_score?: number
}

export interface EvaluationSummary {
  recent_runs: EvaluationRunSummary[]
  latest_run: EvaluationRunSummary | null
  best_run: EvaluationRunSummary | null
  worst_run: EvaluationRunSummary | null
  benchmark: {
    benchmark_score: number
    benchmark_pass_rate: number
    cases: EvaluationBenchmarkCase[]
    benchmark_score_trend: Array<{ date: string; score: number }>
  }
  report_samples: EvaluationReportSample[]
  quality_overview: QualityOverview
}

export interface EvaluationRunSummary {
  run_id: number
  date: string
  status: string
  publish_grade: string
  selected_count: number
  section_coverage: number
  verified_image_count: number
  round_count: number
  content_score: number
  image_score: number
  relevance_score: number
  stability_score: number
  daily_report_score: number
  policy_gap_reason: string | null
  image_gap_reason: string | null
}

export interface EvaluationBenchmarkCase {
  name: string
  passed: boolean
  note: string
  daily_report_score: number
  content_score: number
  image_score: number
  relevance_score: number
  stability_score: number
}

export interface EvaluationReportSample {
  report_id: number
  report_date: string
  title: string
  publish_grade: string
  selected_count: number
  verified_image_count: number
  sections: string[]
}

export interface User {
  id: number
  email: string
  is_admin: boolean
  favorite_report_ids?: number[]
  favorite_conversation_ids?: number[]
}

export interface Conversation {
  id: number
  title: string
  archived: boolean
  retrieval_mode: string
  last_message_at: string
  favorited?: boolean
}

export interface Message {
  id: number
  role: string
  content: string
  citations: Array<Record<string, unknown>>
  retrieval_mode: string
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
}

export interface ChatResponse {
  conversation_id?: number
  user_message: Message
  assistant_message: Message
  content?: string
  citations?: Array<Record<string, unknown>>
  retrieval_mode?: string
}
