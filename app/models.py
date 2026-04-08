from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    include_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    exclude_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    must_include_any: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    must_exclude_any: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    soft_signals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_tier: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    rss_or_listing_url: Mapped[str | None] = mapped_column(String(500))
    crawl_mode: Mapped[str] = mapped_column(String(32), default="rss", nullable=False)
    use_direct_source: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_images: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    language: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(8))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RetrievalRun(TimestampMixin, Base):
    __tablename__ = "retrieval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_date: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    shadow_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    query_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extracted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    debug_payload: Mapped[dict | None] = mapped_column(JSON)

    queries: Mapped[list["RetrievalQuery"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    articles: Mapped[list["Article"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    report: Mapped["Report | None"] = relationship(back_populates="retrieval_run", uselist=False)
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="retrieval_run", cascade="all, delete-orphan")


class RetrievalQuery(TimestampMixin, Base):
    __tablename__ = "retrieval_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    section: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="web", nullable=False)
    response_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filters: Mapped[dict | None] = mapped_column(JSON)

    run: Mapped["RetrievalRun"] = relationship(back_populates="queries")
    candidates: Mapped[list["RetrievalCandidate"]] = relationship(back_populates="query", cascade="all, delete-orphan")


class RetrievalCandidate(TimestampMixin, Base):
    __tablename__ = "retrieval_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    query_id: Mapped[int | None] = mapped_column(ForeignKey("retrieval_queries.id"))
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="discovered", nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

    query: Mapped["RetrievalQuery | None"] = relationship(back_populates="candidates")


class Article(TimestampMixin, Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("run_id", "url", name="uq_articles_run_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="zh", nullable=False)
    country: Mapped[str | None] = mapped_column(String(8))
    source_name: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1000))
    summary: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    raw_markdown: Mapped[str | None] = mapped_column(Text)
    raw_html: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    cluster_key: Mapped[str | None] = mapped_column(String(255))
    freshness_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_trust_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    research_value_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    combined_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

    run: Mapped["RetrievalRun"] = relationship(back_populates="articles")
    images: Mapped[list["ArticleImage"]] = relationship(back_populates="article", cascade="all, delete-orphan")


class ArticleCluster(TimestampMixin, Base):
    __tablename__ = "article_clusters"
    __table_args__ = (UniqueConstraint("run_id", "cluster_key", name="uq_clusters_run_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    cluster_key: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"))
    article_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Report(TimestampMixin, Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_run_id: Mapped[int | None] = mapped_column(ForeignKey("retrieval_runs.id"))
    debug_url: Mapped[str | None] = mapped_column(String(1000))
    error_message: Mapped[str | None] = mapped_column(Text)

    retrieval_run: Mapped["RetrievalRun | None"] = relationship(back_populates="report")
    items: Mapped[list["ReportItem"]] = relationship(back_populates="report", cascade="all, delete-orphan")

    @property
    def publish_grade(self) -> str:
        return self.status

    @property
    def round_count(self) -> int:
        payload = (self.retrieval_run.debug_payload if self.retrieval_run and self.retrieval_run.debug_payload else {}) or {}
        return int(payload.get("round_count", 1) or 1)

    @property
    def supervisor_actions(self) -> list[dict]:
        payload = (self.retrieval_run.debug_payload if self.retrieval_run and self.retrieval_run.debug_payload else {}) or {}
        return list(payload.get("supervisor_actions", []) or [])

    @property
    def hero_image(self) -> dict | None:
        for item in sorted(self.items, key=lambda row: row.rank):
            if item.image_url and item.has_verified_image:
                return {
                    "image_url": item.image_url,
                    "image_caption": item.image_caption,
                    "image_source_url": item.image_source_url,
                    "title": item.title,
                    "section": item.section,
                }
        if not self.items:
            return None
        first = sorted(self.items, key=lambda row: row.rank)[0]
        if not first.image_url:
            return None
        return {
            "image_url": first.image_url,
            "image_caption": first.image_caption,
            "image_source_url": first.image_source_url,
            "title": first.title,
            "section": first.section,
        }

    @property
    def image_review_summary(self) -> dict:
        total = len(self.items)
        verified = sum(1 for item in self.items if item.has_verified_image)
        return {
            "selected_count": total,
            "verified_image_count": verified,
            "items_without_image": max(total - verified, 0),
        }


class ReportItem(TimestampMixin, Base):
    __tablename__ = "report_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), nullable=False)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"))
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    research_signal: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1000))
    image_source_url: Mapped[str | None] = mapped_column(String(1000))
    image_origin_type: Mapped[str | None] = mapped_column(String(64))
    image_caption: Mapped[str | None] = mapped_column(Text)
    image_relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    has_verified_image: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visual_verdict: Mapped[str | None] = mapped_column(String(32))
    context_verdict: Mapped[str | None] = mapped_column(String(32))
    selected_for_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    image_reason: Mapped[str | None] = mapped_column(Text)
    window_bucket: Mapped[str] = mapped_column(String(32), default="primary_24h", nullable=False)
    citations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    combined_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    report: Mapped["Report"] = relationship(back_populates="items")

    @property
    def visual_score(self) -> float:
        verdict = (self.visual_verdict or "").lower()
        if verdict in {"pass", "verified", "trusted"}:
            return 0.9
        if verdict in {"borderline", "fallback"}:
            return 0.65
        if verdict in {"reject", "invalid"}:
            return 0.1
        return round(float(self.image_relevance_score or 0.0), 4)

    @property
    def context_score(self) -> float:
        verdict = (self.context_verdict or "").lower()
        if verdict in {"pass", "verified", "matched"}:
            return 0.9
        if verdict in {"borderline", "fallback"}:
            return 0.65
        if verdict in {"reject", "mismatch"}:
            return 0.1
        return round(float(self.image_relevance_score or 0.0), 4)

    @property
    def final_image_score(self) -> float:
        return round((self.visual_score + self.context_score + float(self.image_relevance_score or 0.0)) / 3.0, 4)


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    retrieval_run_id: Mapped[int | None] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    stage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), default="daily_report", nullable=False)
    finished_reason: Mapped[str | None] = mapped_column(String(32))
    total_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memory_snapshot: Mapped[dict | None] = mapped_column(JSON)
    debug_payload: Mapped[dict | None] = mapped_column(JSON)

    retrieval_run: Mapped["RetrievalRun | None"] = relationship(back_populates="agent_runs")
    steps: Mapped[list["AgentStep"]] = relationship(back_populates="agent_run", cascade="all, delete-orphan")


class AgentStep(TimestampMixin, Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(255))
    round_index: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    decision_type: Mapped[str | None] = mapped_column(String(64))
    decision_summary: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fallback_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_ref_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    output_ref_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    input_payload: Mapped[dict | None] = mapped_column(JSON)
    output_payload: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    # New fields for agent observability
    thought: Mapped[str | None] = mapped_column(Text)  # LLM's free-text reasoning
    tool_name: Mapped[str | None] = mapped_column(String(64))  # tool called this step
    harness_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    agent_run: Mapped["AgentRun"] = relationship(back_populates="steps")


class ArticleImage(TimestampMixin, Base):
    __tablename__ = "article_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False)
    image_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    image_source_url: Mapped[str | None] = mapped_column(String(1000))
    image_origin_type: Mapped[str] = mapped_column(String(64), nullable=False)
    image_relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    image_caption: Mapped[str | None] = mapped_column(Text)
    image_license_note: Mapped[str | None] = mapped_column(Text)
    visual_verdict: Mapped[str | None] = mapped_column(String(32))
    context_verdict: Mapped[str | None] = mapped_column(String(32))
    review_model: Mapped[str | None] = mapped_column(String(255))
    selected_for_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    image_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="candidate", nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    article: Mapped["Article"] = relationship(back_populates="images")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    user: Mapped["User"] = relationship(back_populates="sessions")


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), default="local_first", nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), default="local_first", nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class FavoriteReport(TimestampMixin, Base):
    __tablename__ = "favorite_reports"
    __table_args__ = (UniqueConstraint("user_id", "report_id", name="uq_favorite_report"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), nullable=False)


class FavoriteConversation(TimestampMixin, Base):
    __tablename__ = "favorite_conversations"
    __table_args__ = (UniqueConstraint("user_id", "conversation_id", name="uq_favorite_conversation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)


class QualityFeedback(TimestampMixin, Base):
    __tablename__ = "quality_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_domain: Mapped[str | None] = mapped_column(String(255))
    target_title: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
