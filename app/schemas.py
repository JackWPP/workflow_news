from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReportItemOut(ORMModel):
    id: int
    section: str
    rank: int
    title: str
    source_name: str
    source_url: str
    published_at: datetime | None
    summary: str
    research_signal: str
    image_url: str | None
    image_source_url: str | None
    image_origin_type: str | None
    image_caption: str | None
    image_relevance_score: float
    has_verified_image: bool
    visual_verdict: str | None
    context_verdict: str | None
    visual_score: float = 0.0
    context_score: float = 0.0
    final_image_score: float = 0.0
    selected_for_publish: bool
    image_reason: str | None
    window_bucket: str
    citations: list[dict[str, Any]]
    combined_score: float
    decision_trace: dict[str, Any] = Field(default_factory=dict)
    language: str = "zh"


class ReportOut(ORMModel):
    id: int
    report_date: date
    status: str
    title: str
    markdown_content: str
    summary: str | None
    pipeline_version: str
    debug_url: str | None
    error_message: str | None
    publish_grade: str = "partial"
    round_count: int = 1
    supervisor_actions: list[dict[str, Any]] = Field(default_factory=list)
    hero_image: dict[str, Any] | None = None
    image_review_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    report_type: str = "global"
    categories: list[str] = Field(default_factory=lambda: ["高材制造", "清洁能源", "AI"])
    english_section_count: int = 0
    chinese_section_count: int = 0
    overall_score: float | None = None


class ReportDetailOut(ReportOut):
    items: list[ReportItemOut] = Field(default_factory=list)


class RetrievalRunOut(ORMModel):
    id: int
    run_date: datetime
    started_at: datetime
    finished_at: datetime | None
    status: str
    shadow_mode: bool
    query_count: int
    candidate_count: int
    extracted_count: int
    error_message: str | None
    debug_payload: dict[str, Any] | None


class SourceOut(ORMModel):
    id: int
    name: str
    domain: str
    type: str
    priority: int
    tags: list[str]
    include_rules: list[str]
    exclude_rules: list[str]
    must_include_any: list[str]
    must_exclude_any: list[str]
    soft_signals: list[str]
    source_tier: str
    rss_or_listing_url: str | None
    crawl_mode: str
    use_direct_source: bool
    allow_images: bool
    language: str | None
    country: str | None
    enabled: bool


class SourceUpdate(BaseModel):
    name: str
    domain: str
    type: str
    priority: int = 50
    tags: list[str] = Field(default_factory=list)
    include_rules: list[str] = Field(default_factory=list)
    exclude_rules: list[str] = Field(default_factory=list)
    must_include_any: list[str] = Field(default_factory=list)
    must_exclude_any: list[str] = Field(default_factory=list)
    soft_signals: list[str] = Field(default_factory=list)
    source_tier: str = "unknown"
    rss_or_listing_url: str | None = None
    crawl_mode: str = "rss"
    use_direct_source: bool = False
    allow_images: bool = True
    language: str | None = None
    country: str | None = None
    enabled: bool = True


class SourceRulesPayload(BaseModel):
    sources: list[SourceUpdate]


class ReportRunRequest(BaseModel):
    shadow_mode: bool | None = None
    mode: str = "publish"
    report_type: str = "global"


class ReportSettingsOut(BaseModel):
    report_hour: int
    report_minute: int
    ai_report_enabled: bool = True
    ai_report_hour: int
    ai_report_minute: int
    ai_rss_feed_url: str
    shadow_mode: bool
    scrape_timeout_seconds: int
    scrape_concurrency: int
    max_extractions_per_run: int
    report_primary_model: str
    report_fallback_model: str


class ReportSettingsUpdate(BaseModel):
    report_hour: int
    report_minute: int
    ai_report_enabled: bool = True
    ai_report_hour: int
    ai_report_minute: int
    ai_rss_feed_url: str
    shadow_mode: bool
    scrape_timeout_seconds: int
    scrape_concurrency: int
    max_extractions_per_run: int
    report_primary_model: str
    report_fallback_model: str


class RetrievalCandidateOut(ORMModel):
    id: int
    query_id: int | None
    url: str
    title: str
    domain: str
    section: str
    language: str
    source_type: str
    source_name: str | None
    status: str
    rejection_reason: str | None
    image_url: str | None
    published_at: datetime | None
    metadata_json: dict[str, Any] | None


class RetrievalQueryOut(ORMModel):
    id: int
    section: str
    language: str
    query_text: str
    target_type: str
    response_status: str
    result_count: int
    filters: dict[str, Any] | None


class UserOut(ORMModel):
    id: int
    email: str
    is_admin: bool


class AuthRequest(BaseModel):
    email: str
    password: str


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationOut(ORMModel):
    id: int
    title: str
    archived: bool
    retrieval_mode: str
    last_message_at: datetime


class MessageCreate(BaseModel):
    content: str


class MessageOut(ORMModel):
    id: int
    role: str
    content: str
    citations: list[dict[str, Any]]
    retrieval_mode: str
    created_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class ChatMessageResponse(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut


class ChatPromptRequest(BaseModel):
    conversation_id: int | None = None
    content: str


class FavoriteResponse(BaseModel):
    status: str
    item_id: int


class QualityFeedbackCreate(BaseModel):
    target_type: str
    target_id: int
    label: str
    reason: str | None = None
    note: str | None = None


class QualityFeedbackOut(ORMModel):
    id: int
    target_type: str
    target_id: int
    target_domain: str | None
    target_title: str | None
    label: str
    reason: str | None
    note: str | None
    created_by: int
    created_at: datetime
