from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
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
from app.services.pipeline import NativeReportPipeline
from app.services.repository import (
    create_quality_feedback,
    favorite_conversation_ids,
    favorite_report_ids,
    get_conversation,
    get_latest_report_for_date,
    get_report_by_id,
    get_report_settings,
    get_quality_overview,
    list_conversations,
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


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.timezone)
pipeline = NativeReportPipeline()
chat_service = ChatService()
SESSION_COOKIE = "session_token"
FRONTEND_DIR = Path("frontend/dist")
LEGACY_STATIC_DIR = Path("static")


async def scheduled_report_run():
    logger.info("Starting scheduled native report run.")
    with session_scope() as session:
        await pipeline.run(session, shadow_mode=None)
    logger.info("Scheduled native report run finished.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    trigger = CronTrigger(hour=settings.report_hour, minute=settings.report_minute, timezone=settings.timezone)
    scheduler.add_job(scheduled_report_run, trigger, id="daily_native_report", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started. Job scheduled for %02d:%02d daily.", settings.report_hour, settings.report_minute)
    yield
    scheduler.shutdown()


app = FastAPI(title="Workflow News Native Pipeline", lifespan=lifespan)


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


@app.post("/api/reports/run", response_model=ReportDetailOut)
async def run_report(payload: ReportRunRequest):
    logger.info("Manual native report run requested.")
    with session_scope() as session:
        report = await pipeline.run(
            session,
            shadow_mode=payload.shadow_mode,
        )
        hydrated = get_report_by_id(session, report.id)
        return ReportDetailOut.model_validate(hydrated)


@app.get("/api/reports/today", response_model=ReportDetailOut)
async def get_today_report():
    with session_scope() as session:
        report = get_latest_report_for_date(session, now_local().date())
        if report is None:
            raise HTTPException(status_code=404, detail="No report found for today")
        return ReportDetailOut.model_validate(report)


@app.get("/api/reports")
async def get_report_list(limit: int = 30):
    with session_scope() as session:
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
        return {"runs": [RetrievalRunOut.model_validate(run).model_dump() for run in list_retrieval_runs(session)]}


@app.get("/api/retrieval-runs/{run_id}", response_model=RetrievalRunOut)
async def get_retrieval_run(run_id: int, request: Request):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        run = session.get(RetrievalRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Retrieval run not found")
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
        payload = get_report_settings(session) or {
            "report_hour": settings.report_hour,
            "report_minute": settings.report_minute,
            "shadow_mode": settings.shadow_mode,
            "scrape_timeout_seconds": settings.scrape_timeout_seconds,
            "scrape_concurrency": settings.scrape_concurrency,
            "max_extractions_per_run": settings.max_extractions_per_run,
            "report_primary_model": settings.report_primary_model,
            "report_fallback_model": settings.report_fallback_model,
        }
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


@app.get("/api/admin/quality-overview")
async def get_admin_quality_overview(request: Request, days: int = 7):
    with session_scope() as session:
        _admin_user_or_403(session, request)
        return get_quality_overview(session, days=max(1, min(days, 30)))


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
        report = get_latest_report_for_date(session, now_local().date())
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
        report = get_latest_report_for_date(session, target_date)
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
    report = await run_report(ReportRunRequest(shadow_mode=False))
    return {
        "status": "success",
        "message": "Native report regenerated successfully",
        "report_id": report.id,
        "report_status": report.status,
    }


app.mount(
    "/",
    StaticFiles(directory=str(FRONTEND_DIR if FRONTEND_DIR.exists() else LEGACY_STATIC_DIR), html=True),
    name="static",
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
