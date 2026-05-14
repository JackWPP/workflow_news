from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from sqlalchemy import select, text
import mimetypes
from pathlib import Path

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

from app.bootstrap import init_db
from app.config import settings
from app.database import session_scope
from app.models import FavoriteConversation, FavoriteReport, RetrievalRun
from app.schemas import (
    AuthRequest,
    ChatMessageResponse,
    ChatPromptRequest,
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    FavoriteResponse,
    MessageCreate,
    MessageOut,
    QualityFeedbackCreate,
    QualityFeedbackOut,
    ReportDetailOut,
    ReportItemOut,
    ReportRunRequest,
    ReportSettingsOut,
    ReportSettingsUpdate,
    RetrievalCandidateOut,
    RetrievalQueryOut,
    RetrievalRunOut,
    SourceOut,
    SourceRulesPayload,
    UserOut,
)
from app.services.auth import create_login_session, get_current_user, logout_session, register_user
from app.services.chat import ChatService, append_message, create_conversation
from app.services.ai_rss_pipeline import AiRssDailyPipeline, DEFAULT_AI_FEED_URL
from app.services.daily_report_agent import DailyReportAgent
from app.services.evaluation import enrich_debug_payload
# pipeline kept for direct admin access (DailyReportAgent uses it as fallback internally)
from app.services.pipeline import NativeReportPipeline
from app.services.repository import (
    get_combined_report_for_date,
    create_quality_feedback,
    favorite_conversation_ids,
    favorite_report_ids,
    get_conversation,
    get_latest_report_for_date,
    get_report_by_id,
    get_report_settings,
    get_evaluation_summary,
    get_quality_overview,
    list_conversations,
    list_combined_reports,
    list_history_dates,
    list_quality_feedback,
    list_reports,
    list_retrieval_candidates,
    list_retrieval_queries,
    list_retrieval_runs,
    list_sources,
    replace_sources,
    update_report_settings,
)
from app.utils import now_local


from app.log_context import run_id_var as _run_id_var, request_id_var as _request_id_var


class _RunIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get()  # type: ignore[attr-defined]
        record.request_id = _request_id_var.get()  # type: ignore[attr-defined]
        return True


def _setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    if settings.log_format == "json":
        class JSONFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                entry: dict[str, object] = {
                    "ts": self.formatTime(record, self.datefmt),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                }
                rid = getattr(record, "run_id", None)
                if rid is not None:
                    entry["run_id"] = rid
                req_id = getattr(record, "request_id", None)
                if req_id is not None:
                    entry["request_id"] = req_id
                if record.exc_info and record.exc_info[0]:
                    entry["exc"] = self.formatException(record.exc_info)
                return json.dumps(entry, ensure_ascii=False)
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logging.root.handlers = [handler]
        logging.root.setLevel(level)
    else:
        logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")
    logging.root.addFilter(_RunIDFilter())


_setup_logging()
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.timezone)
# Use DailyReportAgent as the primary pipeline (falls back to NativeReportPipeline on error)
pipeline = DailyReportAgent() if settings.agent_mode else NativeReportPipeline()
ai_pipeline = AiRssDailyPipeline()
chat_service = ChatService()
SESSION_COOKIE = "session_token"
FRONTEND_DIR = Path("frontend/dist")
LEGACY_STATIC_DIR = Path("static")


def _default_report_settings() -> dict[str, object]:
    return {
        "report_hour": settings.report_hour,
        "report_minute": settings.report_minute,
        "ai_report_enabled": True,
        "ai_report_hour": settings.report_hour,
        "ai_report_minute": min(settings.report_minute + 5, 59),
        "ai_rss_feed_url": DEFAULT_AI_FEED_URL,
        "shadow_mode": settings.shadow_mode,
        "scrape_timeout_seconds": settings.scrape_timeout_seconds,
        "scrape_concurrency": settings.scrape_concurrency,
        "max_extractions_per_run": settings.max_extractions_per_run,
        "report_primary_model": settings.report_primary_model,
        "report_fallback_model": settings.report_fallback_model,
    }


async def scheduled_report_run():
    logger.info("Starting scheduled native report run.")
    if isinstance(pipeline, DailyReportAgent):
        await pipeline.run(shadow_mode=None)
    else:
        with session_scope() as session:
            await pipeline.run(session, shadow_mode=None)
    logger.info("Scheduled native report run finished.")


async def scheduled_ingester_run():
    from app.services.ingester import ContinuousIngester
    logger.info("Starting hourly ingester run.")
    try:
        ingester = ContinuousIngester()
        count = await ingester.run()
        logger.info("Hourly ingester finished: %d new articles.", count)
    except Exception as exc:
        logger.error("Hourly ingester failed: %s", exc, exc_info=True)


async def scheduled_ai_report_run():
    logger.info("Starting scheduled AI RSS report run.")
    with session_scope() as session:
        report_settings = _default_report_settings()
        report_settings.update(get_report_settings(session) or {})
        feed_url = str(report_settings.get("ai_rss_feed_url") or DEFAULT_AI_FEED_URL)
        await ai_pipeline.run(session, feed_url=feed_url)
    logger.info("Scheduled AI RSS report run finished.")


async def scheduled_lab_report_run():
    logger.info("Starting scheduled lab report run.")
    try:
        from app.services.lab_report_composer import LabReportComposer
        composer = LabReportComposer()
        report = composer.compose()
        if report:
            logger.info("Lab report generated: %s", report.title)
        else:
            logger.warning("Lab report generation skipped (no content).")
    except Exception as exc:
        logger.error("Lab report generation failed: %s", exc, exc_info=True)

    # Also trigger AI report
    try:
        await scheduled_ai_report_run()
    except Exception as exc:
        logger.error("AI report co-trigger failed: %s", exc, exc_info=True)


async def scheduled_weixin_ingester_run():
    logger.info("Starting WeChat ingester run.")
    # Try API-based sync first
    try:
        from app.services.wechat_client import get_credentials, sync_account_articles, search_account
        creds = get_credentials()
        if creds:
            accounts = await search_account("英蓝云展")
            if accounts:
                added = await sync_account_articles(accounts[0]["fakeid"], accounts[0].get("nickname", "英蓝云展"))
                logger.info("WeChat API sync: %d new articles.", added)
    except Exception as exc:
        logger.warning("WeChat API sync failed: %s", exc)

    # Scrape pending articles
    try:
        from app.services.ingester import ingest_weixin_articles
        count = await ingest_weixin_articles()
        logger.info("WeChat ingester finished: %d articles scraped.", count)
    except Exception as exc:
        logger.error("WeChat ingester failed: %s", exc, exc_info=True)


def _run_alembic_migrations() -> None:
    from app.database import _is_sqlite
    if _is_sqlite:
        return
    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command
        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations applied successfully.")
    except Exception as exc:
        logger.warning("Alembic migration skipped (non-fatal): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_alembic_migrations()
    init_db()
    report_settings = _default_report_settings()
    with session_scope() as session:
        report_settings.update(get_report_settings(session) or {})

    trigger = CronTrigger(hour=report_settings["report_hour"], minute=report_settings["report_minute"], timezone=settings.timezone)
    scheduler.add_job(scheduled_report_run, trigger, id="daily_native_report", replace_existing=True)
    scheduler.add_job(
        scheduled_ai_report_run,
        CronTrigger(
            hour=report_settings["ai_report_hour"],
            minute=report_settings["ai_report_minute"],
            timezone=settings.timezone,
        ),
        id="daily_ai_report",
        replace_existing=True,
    )
    if not report_settings.get("ai_report_enabled", True):
        scheduler.pause_job("daily_ai_report")
    scheduler.add_job(scheduled_ingester_run, CronTrigger(hour="*"), id="hourly_ingester", replace_existing=True)
    scheduler.add_job(
        scheduled_lab_report_run,
        CronTrigger(hour=report_settings["report_hour"], minute=report_settings["report_minute"] + 30, timezone=settings.timezone),
        id="daily_lab_report",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_weixin_ingester_run,
        CronTrigger(hour="*/6"),
        id="weixin_ingester",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started. Job scheduled for %02d:%02d daily.", settings.report_hour, settings.report_minute)
    yield
    scheduler.shutdown()


app = FastAPI(title="Workflow News Native Pipeline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/assets/", "/favicon")):
            return await call_next(request)
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:8])
        _request_id_var.set(request_id)
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            "HTTP %s %s -> %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestLoggingMiddleware)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.session_days * 24 * 60 * 60,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def _current_user_or_401(session, request: Request):
    user = get_current_user(session, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _admin_user_or_403(session, request: Request):
    user = _current_user_or_401(session, request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _serialize_me(session, user) -> dict:
    return {
        **UserOut.model_validate(user).model_dump(),
        "favorite_report_ids": favorite_report_ids(session, user.id),
        "favorite_conversation_ids": favorite_conversation_ids(session, user.id),
    }


@app.post("/api/auth/register")
async def auth_register(payload: AuthRequest, response: Response):
    try:
        with session_scope() as session:
            user = register_user(session, payload.email, payload.password)
            _, auth_session = create_login_session(session, payload.email, payload.password)
            result = _serialize_me(session, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_session_cookie(response, auth_session.token)
    return result


@app.post("/api/auth/login")
async def auth_login(payload: AuthRequest, response: Response):
    try:
        with session_scope() as session:
            user, auth_session = create_login_session(session, payload.email, payload.password)
            result = _serialize_me(session, user)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, auth_session.token)
    return result


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    with session_scope() as session:
        logout_session(session, request.cookies.get(SESSION_COOKIE))
    _clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/api/me")
async def read_me(request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        return _serialize_me(session, user)


import asyncio as _asyncio
import json as _json_mod

# ── 报告运行状态管理（SSE 实时推送） ──────────────────────────
_run_event_queues: dict[int, _asyncio.Queue] = {}
_running_task: _asyncio.Task | None = None
_running_agent_run_id: int | None = None


@app.post("/api/reports/run")
async def run_report(payload: ReportRunRequest):
    """异步启动报告生成，立即返回 run IDs。前端通过 SSE 端点跟踪进度。"""
    global _running_task, _running_agent_run_id

    if _running_task and not _running_task.done():
        raise HTTPException(status_code=409, detail="A report is already being generated")

    logger.info("Manual report run requested (async mode, report_type=%s).", payload.report_type)

    # 预创建 DB 记录以获取 ID
    with session_scope() as session:
        from app.models import AgentRun
        run_record = RetrievalRun(
            run_date=now_local(),
            status="running",
        )
        session.add(run_record)
        session.flush()
        run_id = run_record.id
        session.commit()

    # 创建事件队列
    event_queue: _asyncio.Queue = _asyncio.Queue()

    async def _run_pipeline():
        global _running_task, _running_agent_run_id
        try:
            if payload.report_type == "lab":
                from app.services.lab_report_composer import LabReportComposer
                composer = LabReportComposer()
                report = composer.compose()
                if report is None:
                    raise RuntimeError("No content available for lab report")
                # Also trigger AI report
                try:
                    with session_scope() as ai_session:
                        report_settings = get_report_settings(ai_session) or _default_report_settings()
                        await ai_pipeline.run(
                            ai_session,
                            feed_url=str(report_settings.get("ai_rss_feed_url") or DEFAULT_AI_FEED_URL),
                        )
                except Exception as ai_exc:
                    logger.warning("AI report co-trigger failed (non-fatal): %s", ai_exc)
            elif payload.report_type == "ai":
                with session_scope() as session:
                    report_settings = get_report_settings(session) or _default_report_settings()
                    report = await ai_pipeline.run(
                        session,
                        run_id=run_id,
                        feed_url=str(report_settings.get("ai_rss_feed_url") or DEFAULT_AI_FEED_URL),
                    )
            else:
                if isinstance(pipeline, DailyReportAgent):
                    report = await pipeline.run(
                        run_id=run_id,
                        shadow_mode=payload.shadow_mode,
                        mode=payload.mode,
                        event_queue=event_queue,
                    )
                else:
                    with session_scope() as session:
                        report = await pipeline.run(
                            session,
                            shadow_mode=payload.shadow_mode,
                            mode=payload.mode,
                        )
            event_queue.put_nowait({
                "type": "complete",
                "report_id": report.id,
                "status": report.status,
            })
        except Exception as exc:
            logger.error("Pipeline failed: %s", exc, exc_info=True)
            event_queue.put_nowait({"type": "error", "message": str(exc)[:500]})
        finally:
            # 发送结束标记
            event_queue.put_nowait(None)
            _running_task = None
            _running_agent_run_id = None
            # 清理队列引用（延迟清理以允许 SSE 消费完）
            await _asyncio.sleep(30)
            _run_event_queues.pop(run_id, None)

    _running_task = _asyncio.create_task(_run_pipeline())
    _run_event_queues[run_id] = event_queue
    _running_agent_run_id = run_id

    return JSONResponse({"run_id": run_id, "status": "running"})


@app.get("/api/reports/run/{run_id}/stream")
async def stream_report_progress(run_id: int):
    """SSE 端点：实时推送报告生成进度。"""
    event_queue = _run_event_queues.get(run_id)
    if not event_queue:
        raise HTTPException(status_code=404, detail="No active run found for this ID")

    async def event_generator():
        try:
            while True:
                try:
                    event = await _asyncio.wait_for(event_queue.get(), timeout=120.0)
                except _asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
                    continue

                if event is None:
                    # 结束标记
                    yield "event: done\ndata: {}\n\n"
                    break

                event_type = event.get("type", "step")
                yield f"event: {event_type}\ndata: {_json_mod.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except _asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/reports/run/status")
async def get_run_status():
    """查询当前是否有运行中的报告生成。"""
    if _running_task and not _running_task.done():
        return {"status": "running", "run_id": _running_agent_run_id}
    return {"status": "idle", "run_id": None}


@app.get("/api/reports/today", response_model=ReportDetailOut)
async def get_today_report(view: str = "combined", report_type: str | None = None):
    with session_scope() as session:
        if report_type == "lab":
            report = get_latest_report_for_date(session, now_local().date(), report_type="lab")
        elif report_type == "ai":
            report = get_latest_report_for_date(session, now_local().date(), report_type="ai")
        elif view == "combined":
            report = get_combined_report_for_date(session, now_local().date())
        else:
            report = get_latest_report_for_date(session, now_local().date())
        if report is None:
            raise HTTPException(status_code=404, detail="No report found for today")
        return ReportDetailOut.model_validate(report)


@app.get("/api/reports")
async def get_report_list(limit: int = 30, view: str = "combined", report_type: str | None = None):
    with session_scope() as session:
        if report_type:
            reports = list_reports(session, limit=max(1, min(limit, 100)), report_type=report_type)
        elif view == "combined":
            reports = list_combined_reports(session, limit=max(1, min(limit, 100)))
        else:
            reports = list_reports(session, limit=max(1, min(limit, 100)))
        return {"reports": [ReportDetailOut.model_validate(report).model_dump(mode="json") for report in reports]}


@app.get("/api/reports/{report_id}", response_model=ReportDetailOut)
async def get_report(report_id: int):
    with session_scope() as session:
        report = get_report_by_id(session, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return ReportDetailOut.model_validate(report)


@app.get("/api/reports/{report_id}/items")
async def get_report_items(report_id: int):
    with session_scope() as session:
        report = get_report_by_id(session, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return {"items": [ReportItemOut.model_validate(item).model_dump() for item in report.items]}


@app.get("/api/retrieval-runs")
async def get_retrieval_run_list(request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        runs = list_retrieval_runs(session)
        for run in runs:
            run.debug_payload = enrich_debug_payload(run.debug_payload, report_status=run.status)
        return {"runs": [RetrievalRunOut.model_validate(run).model_dump() for run in runs]}


@app.get("/api/retrieval-runs/{run_id}", response_model=RetrievalRunOut)
async def get_retrieval_run(run_id: int, request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        run = session.get(RetrievalRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Retrieval run not found")
        run.debug_payload = enrich_debug_payload(run.debug_payload, report_status=run.status)
        return RetrievalRunOut.model_validate(run)


@app.get("/api/retrieval-runs/{run_id}/queries")
async def get_retrieval_queries(run_id: int, request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        return {"queries": [RetrievalQueryOut.model_validate(query).model_dump() for query in list_retrieval_queries(session, run_id)]}


@app.get("/api/retrieval-runs/{run_id}/candidates")
async def get_retrieval_candidates(run_id: int, request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        return {
            "candidates": [
                RetrievalCandidateOut.model_validate(candidate).model_dump()
                for candidate in list_retrieval_candidates(session, run_id)
            ]
        }


@app.get("/api/admin/source-rules")
async def get_source_rules(request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        return {"sources": [SourceOut.model_validate(source).model_dump() for source in list_sources(session)]}


@app.put("/api/admin/source-rules")
async def put_source_rules(payload: SourceRulesPayload, request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        sources = replace_sources(session, [source.model_dump() for source in payload.sources])
        return {"sources": [SourceOut.model_validate(source).model_dump() for source in sources]}


@app.get("/api/admin/report-settings", response_model=ReportSettingsOut)
async def get_admin_report_settings(request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        payload = _default_report_settings()
        payload.update(get_report_settings(session) or {})
        return payload


@app.put("/api/admin/report-settings", response_model=ReportSettingsOut)
async def put_admin_report_settings(payload: ReportSettingsUpdate, request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        updated = update_report_settings(session, payload.model_dump())

    scheduler.reschedule_job(
        "daily_native_report",
        trigger=CronTrigger(hour=updated["report_hour"], minute=updated["report_minute"], timezone=settings.timezone),
    )
    scheduler.reschedule_job(
        "daily_ai_report",
        trigger=CronTrigger(hour=updated["ai_report_hour"], minute=updated["ai_report_minute"], timezone=settings.timezone),
    )
    if updated["ai_report_enabled"]:
        scheduler.resume_job("daily_ai_report")
    else:
        scheduler.pause_job("daily_ai_report")
    return updated


@app.get("/api/admin/quality-feedback")
async def get_admin_quality_feedback(request: Request, limit: int = 50):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        items = list_quality_feedback(session, limit=max(1, min(limit, 200)))
        return {"items": [QualityFeedbackOut.model_validate(item).model_dump(mode="json") for item in items]}


@app.post("/api/admin/quality-feedback", response_model=QualityFeedbackOut)
async def post_admin_quality_feedback(payload: QualityFeedbackCreate, request: Request):
    with session_scope() as session:
        user = _admin_user_or_403(session, request)
        try:
            item = create_quality_feedback(
                session,
                user.id,
                payload.target_type,
                payload.target_id,
                payload.label,
                payload.reason,
                payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return QualityFeedbackOut.model_validate(item)


@app.post("/api/admin/wechat-token")
async def set_wechat_token(payload: dict, request: Request):
    """设置微信公众号平台 token 和 cookie。"""
    with session_scope() as session:
        _admin_user_or_403(session, request)
    from app.services.wechat_client import set_credentials
    token = payload.get("token", "").strip()
    cookie = payload.get("cookie", "").strip()
    if not token or not cookie:
        raise HTTPException(status_code=400, detail="token and cookie required")
    set_credentials(token, cookie)
    return {"status": "ok"}


@app.get("/api/admin/wechat-token")
async def get_wechat_token_status(request: Request):
    """检查微信公众号 token 是否已配置。"""
    with session_scope() as session:
        _admin_user_or_403(session, request)
    from app.services.wechat_client import get_credentials
    creds = get_credentials()
    return {"configured": bool(creds)}


@app.delete("/api/admin/wechat-token")
async def clear_wechat_token(request: Request):
    """清除微信公众号 token 和 cookie。"""
    with session_scope() as session:
        _admin_user_or_403(session, request)
    from app.services.wechat_client import clear_credentials
    clear_credentials()
    return {"status": "cleared"}


_wechat_sync_status: dict[str, Any] = {
    "running": False,
    "account": "",
    "pages_done": 0,
    "articles_added": 0,
    "last_page_count": 0,
    "error": "",
    "done": False,
}


@app.post("/api/admin/wechat-sync")
async def sync_wechat_account(payload: dict, request: Request):
    """启动后台公众号文章同步（异步）。"""
    with session_scope() as session:
        _admin_user_or_403(session, request)
    from app.services.wechat_client import search_account, sync_account_articles, get_credentials
    if not get_credentials():
        raise HTTPException(status_code=400, detail="WeChat token not configured")
    if _wechat_sync_status["running"]:
        raise HTTPException(status_code=409, detail="同步任务正在进行中")

    account_name = payload.get("account_name", "英蓝云展")
    fakeid = payload.get("fakeid")
    max_pages = max(1, min(int(payload.get("max_pages", 10)), 20))

    if not fakeid:
        accounts = await search_account(account_name)
        if not accounts:
            raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")
        fakeid = accounts[0]["fakeid"]
        account_name = accounts[0].get("nickname", account_name)

    _wechat_sync_status.update(
        running=True, account=account_name, pages_done=0,
        articles_added=0, last_page_count=0, error="", done=False,
    )

    async def _run_sync():
        try:
            async def on_progress(page: int, added: int, page_count: int):
                _wechat_sync_status["pages_done"] = page
                _wechat_sync_status["articles_added"] = added
                _wechat_sync_status["last_page_count"] = page_count

            added = await sync_account_articles(
                fakeid, account_name, max_pages=max_pages, progress_callback=on_progress,
            )
            _wechat_sync_status["articles_added"] = added

            # Scrape pending articles
            if added > 0:
                try:
                    from app.services.ingester import ingest_weixin_articles
                    await ingest_weixin_articles()
                except Exception as exc:
                    logger.warning("WeChat scrape after sync failed: %s", exc)
        except Exception as exc:
            _wechat_sync_status["error"] = str(exc)[:300]
            logger.error("WeChat background sync failed: %s", exc, exc_info=True)
        finally:
            _wechat_sync_status["running"] = False
            _wechat_sync_status["done"] = True

    import asyncio
    asyncio.create_task(_run_sync())
    return {"status": "started", "account": account_name, "max_pages": max_pages}


@app.get("/api/admin/wechat-sync/status")
async def get_wechat_sync_status(request: Request):
    """查询公众号同步进度。"""
    with session_scope() as session:
        _admin_user_or_403(session, request)
    return dict(_wechat_sync_status)


@app.post("/api/admin/wechat-urls")
async def import_wechat_urls(payload: dict, request: Request):
    """手动导入公众号文章 URL，自动触发爬取。"""
    with session_scope() as session:
        _admin_user_or_403(session, request)

    urls = payload.get("urls", [])
    if not urls:
        raise HTTPException(status_code=400, detail="urls list required")

    from app.models import WeChatArticle
    from app.utils import canonicalize_url

    added = 0
    with session_scope() as session:
        for url in urls:
            if not isinstance(url, str) or "weixin" not in url:
                continue
            normalized = canonicalize_url(url.strip())
            existing = session.scalar(
                select(WeChatArticle).where(WeChatArticle.url == normalized)
            )
            if existing:
                continue
            wa = WeChatArticle(
                url=normalized,
                title=url.split("/")[-1][:200] or "待爬取",
                account_name="英蓝云展",
                scrape_status="pending",
            )
            session.add(wa)
            added += 1
        session.commit()

    if added > 0:
        try:
            from app.services.ingester import ingest_weixin_articles
            await ingest_weixin_articles()
        except Exception as exc:
            logger.warning("WeChat scrape after import failed: %s", exc)

    return {"added": added, "total_submitted": len(urls)}


@app.get("/api/admin/quality-overview")
async def get_admin_quality_overview(request: Request, days: int = 7):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        return get_quality_overview(session, days=max(1, min(days, 30)))


@app.get("/api/admin/evaluation-summary")
async def get_admin_evaluation_summary(request: Request, days: int = 7):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        return get_evaluation_summary(session, days=max(1, min(days, 30)))


@app.post("/api/admin/evaluation/evaluate/{report_id}")
async def trigger_evaluation(report_id: int, request: Request):
    from app.services.eval_runner import EvalRunner
    with session_scope() as session:
        _admin_user_or_403(session, request)
        from app.models import Article, Report
        report = session.get(Report, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        articles = list(
            session.scalars(
                select(Article).where(Article.run_id == report.retrieval_run_id)
            ).all()
        )
        runner = EvalRunner(judge_model="claude-opus-4-7")
        result = await runner.evaluate_report(session, report, articles)
        return result


@app.get("/api/admin/evaluation/dashboard")
async def get_evaluation_dashboard(request: Request, days: int = 30):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        from app.models import EvaluationRun, Report
        from app.utils import now_local
        since = now_local() - timedelta(days=max(days - 1, 0))
        runs = list(
            session.scalars(
                select(EvaluationRun)
                .join(Report)
                .where(Report.report_date >= since.date())
                .order_by(Report.report_date)
            ).all()
        )
        if not runs:
            return {"trend": [], "latest": None, "averages": {}}
        trend = [
            {
                "date": run.evaluated_at.date().isoformat(),
                "weighted_total": run.weighted_total,
                "faithfulness": run.faithfulness_score,
                "coverage": run.coverage_score,
                "dedup": run.dedup_score,
                "fluency": run.fluency_score,
                "research_value": run.research_value_score,
            }
            for run in runs
        ]
        scores = [r.weighted_total for r in runs if r.weighted_total is not None]
        avg = sum(scores) / len(scores) if scores else 0
        return {
            "trend": trend,
            "latest": trend[-1] if trend else None,
            "averages": {
                "avg_weighted_total": round(avg, 2),
                "total_evaluations": len(runs),
            },
        }


@app.get("/api/conversations")
async def get_conversation_list(request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        conversations = list_conversations(session, user.id)
        favorite_ids = set(favorite_conversation_ids(session, user.id))
        return {
            "conversations": [
                {**ConversationOut.model_validate(conversation).model_dump(), "favorited": conversation.id in favorite_ids}
                for conversation in conversations
            ]
        }


@app.post("/api/conversations", response_model=ConversationOut)
async def post_conversation(payload: ConversationCreate, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        conversation = create_conversation(session, user.id, payload.title)
        return ConversationOut.model_validate(conversation)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation_detail(conversation_id: int, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        conversation = get_conversation(session, conversation_id, user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return ConversationDetailOut.model_validate(conversation)


@app.post("/api/conversations/{conversation_id}/messages", response_model=ChatMessageResponse)
async def post_conversation_message(conversation_id: int, payload: MessageCreate, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        conversation = get_conversation(session, conversation_id, user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        user_message = append_message(session, conversation, "user", payload.content)
        answer, citations, mode = await chat_service.build_answer(session, payload.content, conversation.retrieval_mode)
        assistant_message = append_message(session, conversation, "assistant", answer, citations=citations, retrieval_mode=mode)
        return ChatMessageResponse(
            user_message=MessageOut.model_validate(user_message),
            assistant_message=MessageOut.model_validate(assistant_message),
        )


@app.post("/api/chat/stream")
async def post_chat_stream(payload: ChatPromptRequest, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        if payload.conversation_id is None:
            conversation = create_conversation(session, user.id, payload.content[:20] or "新对话")
        else:
            conversation = get_conversation(session, payload.conversation_id, user.id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found")

        user_message = append_message(session, conversation, "user", payload.content)
        answer, citations, mode = await chat_service.build_answer(session, payload.content, conversation.retrieval_mode)
        assistant_message = append_message(session, conversation, "assistant", answer, citations=citations, retrieval_mode=mode)

        body = {
            "conversation_id": conversation.id,
            "user_message": MessageOut.model_validate(user_message).model_dump(mode="json"),
            "assistant_message": MessageOut.model_validate(assistant_message).model_dump(mode="json"),
            "content": answer,
            "citations": citations,
            "retrieval_mode": mode,
        }

    async def event_stream():
        yield f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/favorites/reports/{report_id}", response_model=FavoriteResponse)
async def favorite_report(report_id: int, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        report = get_report_by_id(session, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        exists = session.query(FavoriteReport).filter_by(user_id=user.id, report_id=report_id).first()
        if exists is None:
            session.add(FavoriteReport(user_id=user.id, report_id=report_id))
        return FavoriteResponse(status="favorited", item_id=report_id)


@app.delete("/api/favorites/reports/{report_id}", response_model=FavoriteResponse)
async def unfavorite_report(report_id: int, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        favorite = session.query(FavoriteReport).filter_by(user_id=user.id, report_id=report_id).first()
        if favorite is not None:
            session.delete(favorite)
        return FavoriteResponse(status="unfavorited", item_id=report_id)


@app.post("/api/favorites/conversations/{conversation_id}", response_model=FavoriteResponse)
async def favorite_conversation(conversation_id: int, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        conversation = get_conversation(session, conversation_id, user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        exists = session.query(FavoriteConversation).filter_by(user_id=user.id, conversation_id=conversation_id).first()
        if exists is None:
            session.add(FavoriteConversation(user_id=user.id, conversation_id=conversation_id))
        return FavoriteResponse(status="favorited", item_id=conversation_id)


@app.delete("/api/favorites/conversations/{conversation_id}", response_model=FavoriteResponse)
async def unfavorite_conversation(conversation_id: int, request: Request):
    with session_scope() as session:
        user = _current_user_or_401(session, request)
        favorite = session.query(FavoriteConversation).filter_by(user_id=user.id, conversation_id=conversation_id).first()
        if favorite is not None:
            session.delete(favorite)
        return FavoriteResponse(status="unfavorited", item_id=conversation_id)


@app.get("/api/news/today")
async def read_today_news():
    with session_scope() as session:
        report = get_combined_report_for_date(session, now_local().date())
        if report is None:
            return {"content": None, "date": now_local().date().isoformat(), "status": "missing"}
        return {
            "id": report.id,
            "date": report.report_date.isoformat(),
            "content": report.markdown_content,
            "status": report.status,
            "summary": report.summary,
        }


@app.get("/api/news/history")
async def read_history():
    with session_scope() as session:
        return {"dates": [value.isoformat() for value in list_history_dates(session)]}


@app.get("/api/news/{report_date}")
async def read_news_by_date(report_date: str):
    try:
        target_date = date.fromisoformat(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD") from exc
    with session_scope() as session:
        report = get_combined_report_for_date(session, target_date)
        if report is None:
            raise HTTPException(status_code=404, detail="News not found for this date")
        return {
            "id": report.id,
            "date": report.report_date.isoformat(),
            "content": report.markdown_content,
            "status": report.status,
            "summary": report.summary,
        }


@app.post("/api/regenerate")
async def regenerate_news():
    result = await run_report(ReportRunRequest(shadow_mode=False))
    return result



# ── Agent Trace API ───────────────────────────────────────────────────────────

@app.get("/api/agent-runs")
async def list_agent_runs_api(request: Request, limit: int = 20):
    from app.services.agent_observability import list_agent_runs
    with session_scope() as session:
        _current_user_or_401(session, request)
        return {"runs": list_agent_runs(session, limit=min(limit, 50))}


@app.get("/api/agent-runs/{run_id}")
async def get_agent_run_trace_api(run_id: int, request: Request):
    from app.services.agent_observability import get_agent_run_trace
    with session_scope() as session:
        _current_user_or_401(session, request)
        trace = get_agent_run_trace(session, run_id)
        if trace is None:
            raise HTTPException(status_code=404, detail=f"AgentRun {run_id} not found")
        return trace


@app.get("/api/agent-runs/{run_id}/steps/{step_id}")
async def get_agent_step_detail_api(run_id: int, step_id: int, request: Request):
    from app.services.agent_observability import get_agent_step_detail
    with session_scope() as session:
        _current_user_or_401(session, request)
        detail = get_agent_step_detail(session, step_id)
        if detail is None or detail.get("agent_run_id") != run_id:
            raise HTTPException(status_code=404, detail=f"AgentStep {step_id} not found")
        return detail


# ── Diagnostics API ──────────────────────────────────────────────────────────


@app.get("/api/diagnostics/health")
async def diagnostics_health(deep: bool = False):
    from app.services.bocha_search import BochaSearchClient

    components: dict[str, dict[str, object]] = {}
    statuses: list[str] = []

    # Database
    try:
        t0 = time.monotonic()
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        latency = round((time.monotonic() - t0) * 1000, 1)
        components["database"] = {"status": "ok", "latency_ms": latency}
        statuses.append("ok")
    except Exception as exc:
        components["database"] = {"status": "error", "error": str(exc)[:200]}
        statuses.append("error")

    # DeepSeek API
    ds_key = bool(settings.deepseek_api_key)
    if not ds_key:
        components["deepseek_api"] = {"status": "not_configured", "key_present": False}
        statuses.append("degraded")
    elif deep:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{settings.deepseek_base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                )
                ok = resp.status_code in (200, 401, 403)
                components["deepseek_api"] = {
                    "status": "ok" if ok else "error",
                    "key_present": True,
                    "http_status": resp.status_code,
                }
                statuses.append("ok" if ok else "error")
        except Exception as exc:
            components["deepseek_api"] = {"status": "unreachable", "key_present": True, "error": str(exc)[:200]}
            statuses.append("error")
    else:
        components["deepseek_api"] = {"status": "ok", "key_present": True}
        statuses.append("ok")

    # Bocha API
    bocha = BochaSearchClient()
    if not bocha.enabled:
        components["bocha_api"] = {"status": "not_configured", "key_present": False}
        statuses.append("degraded")
    else:
        components["bocha_api"] = {"status": "ok", "key_present": True}
        statuses.append("ok")

    overall = "healthy"
    if "error" in statuses:
        overall = "unhealthy"
    elif "degraded" in statuses:
        overall = "degraded"

    return {"overall": overall, "components": components}


@app.get("/api/diagnostics/last-run")
async def diagnostics_last_run():
    from app.models import AgentRun, EvaluationRun, Report
    from app.services.evaluation import enrich_debug_payload

    with session_scope() as session:
        run = session.scalars(
            select(RetrievalRun).order_by(RetrievalRun.id.desc()).limit(1)
        ).first()
        if not run:
            return {"error": "no runs found"}

        debug = enrich_debug_payload(run.debug_payload or {})
        agent_run = session.scalars(
            select(AgentRun).where(AgentRun.retrieval_run_id == run.id).order_by(AgentRun.id.desc()).limit(1)
        ).first()
        report = session.scalars(
            select(Report).where(Report.retrieval_run_id == run.id).limit(1)
        ).first()
        eval_run = None
        if report:
            eval_run = session.scalars(
                select(EvaluationRun).where(EvaluationRun.report_id == report.id).order_by(EvaluationRun.id.desc()).limit(1)
            ).first()

        duration = None
        if run.created_at and run.finished_at:
            duration = round((run.finished_at - run.created_at).total_seconds(), 1)

        harness = debug.get("harness_status", {})
        memory = agent_run.memory_snapshot if agent_run else {}

        key_failures: list[dict[str, str]] = []
        pg = debug.get("publish_gate_reason", "")
        if pg and pg != "meets_auto_publish_gate":
            key_failures.append({"code": "publish_gate", "message": pg})
        if debug.get("section_write_timeouts"):
            key_failures.append({"code": "section_timeouts", "message": f"{len(debug['section_write_timeouts'])} section(s) timed out"})
        if debug.get("phase2_rejected_missing_date_count", 0) > 0:
            key_failures.append({"code": "missing_date", "message": f"{debug['phase2_rejected_missing_date_count']} articles rejected for missing date"})
        domain_fails = memory.get("domain_failures", {}) if isinstance(memory, dict) else {}
        for domain, info in domain_fails.items():
            if isinstance(info, dict) and info.get("count", 0) >= 2:
                key_failures.append({"code": "domain_failure", "message": f"{domain}: {info['count']} failures"})

        search_health = memory.get("search_provider_health", {}) if isinstance(memory, dict) else {}

        result: dict[str, object] = {
            "run_id": run.id,
            "agent_run_id": agent_run.id if agent_run else None,
            "run_date": str(run.run_date) if run.run_date else None,
            "status": run.status,
            "finished_reason": agent_run.finished_reason if agent_run else None,
            "duration_seconds": duration,
            "total_steps": agent_run.total_steps if agent_run else debug.get("agent_steps", 0),
            "total_tokens": agent_run.total_tokens if agent_run else 0,
            "article_count": debug.get("selected_count", 0),
            "section_coverage": debug.get("section_coverage", 0),
            "verified_image_count": debug.get("image_selected_count", 0),
            "publish_grade": debug.get("publish_grade"),
            "scores": {
                "content_score": debug.get("content_score"),
                "image_score": debug.get("image_score"),
                "relevance_score": debug.get("relevance_score"),
                "stability_score": debug.get("stability_score"),
                "daily_report_score": debug.get("daily_report_score"),
            },
            "llm_errors": {
                "model_fallbacks": debug.get("model_fallbacks", []),
                "bad_request_count": debug.get("llm_bad_request_count", 0),
                "rate_limit_errors": debug.get("kimi_rate_limit_errors", 0),
                "tool_use_model": debug.get("tool_use_model"),
            },
            "search_health": search_health,
            "key_failures": key_failures,
            "harness": {
                "step_count": harness.get("step_count", 0),
                "search_count": harness.get("search_count", 0),
                "read_count": harness.get("read_count", 0),
                "violations": len(harness.get("violations", [])),
            },
        }
        if eval_run:
            result["evaluation"] = {
                "weighted_total": eval_run.weighted_total,
                "faithfulness": eval_run.faithfulness_score,
                "coverage": eval_run.coverage_score,
            }
        return result


@app.get("/api/diagnostics/llm-metrics")
async def diagnostics_llm_metrics():
    from app.models import AgentRun

    with session_scope() as session:
        run = session.scalars(
            select(AgentRun).order_by(AgentRun.id.desc()).limit(1)
        ).first()
        if not run:
            return {"error": "no agent runs found"}

        dp = run.debug_payload or {}
        return {
            "run_id": run.id,
            "metrics": {
                "tool_use_model": dp.get("tool_use_model"),
                "model_fallbacks": dp.get("model_fallbacks", []),
                "llm_bad_request_count": dp.get("llm_bad_request_count", 0),
                "kimi_rate_limit_errors": dp.get("kimi_rate_limit_errors", 0),
                "tool_use_history_reset_count": dp.get("tool_use_history_reset_count", 0),
                "moonshot_reasoning_history_errors": dp.get("moonshot_reasoning_history_errors", 0),
                "strict_primary_model_enabled": dp.get("strict_primary_model_enabled"),
                "tool_use_fallback_mode": dp.get("tool_use_fallback_mode"),
            },
            "synthesis": {
                "model_used": dp.get("synthesis_model_used"),
                "fallback_triggered": dp.get("synthesis_fallback_triggered"),
            },
            "llm_metrics_on_crash": dp.get("llm_metrics_on_crash"),
        }


@app.get("/api/diagnostics/run/{run_id}/timeline")
async def diagnostics_run_timeline(run_id: int):
    from app.services.agent_observability import get_agent_run_timeline

    with session_scope() as session:
        timeline = get_agent_run_timeline(session, run_id)
        if timeline is None:
            raise HTTPException(status_code=404, detail=f"AgentRun {run_id} not found")
        return timeline


app.mount(
    "/",
    StaticFiles(directory=str(FRONTEND_DIR if FRONTEND_DIR.exists() else LEGACY_STATIC_DIR), html=True),
    name="static",
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)
