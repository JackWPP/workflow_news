from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager

from database import init_db, save_news, get_today_news, get_latest_news_by_date, get_history_dates
from coze import fetch_coze_news

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Scheduler
scheduler = AsyncIOScheduler()

async def scheduled_news_fetch():
    logger.info("Starting scheduled news fetch...")
    content = await fetch_coze_news()
    if content:
        save_news(content)
        logger.info("News fetched and saved successfully.")
    else:
        logger.error("Failed to fetch news.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    
    # Schedule job: Every day at 10:00 AM
    trigger = CronTrigger(hour=10, minute=0)
    scheduler.add_job(scheduled_news_fetch, trigger, id="daily_news_fetch", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started. Job scheduled for 10:00 AM daily.")
    
    yield
    
    # Shutdown
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

# API Endpoints

@app.get("/api/news/today")
async def read_today_news():
    news = get_today_news()
    if not news:
        return {"content": None, "date": datetime.now().strftime("%Y-%m-%d")}
    return news

@app.get("/api/news/history")
async def read_history():
    dates = get_history_dates()
    return {"dates": dates}

@app.get("/api/news/{date}")
async def read_news_by_date(date: str):
    news = get_latest_news_by_date(date)
    if not news:
        raise HTTPException(status_code=404, detail="News not found for this date")
    return news

@app.post("/api/regenerate")
async def regenerate_news():
    logger.info("Manual regeneration requested.")
    content = await fetch_coze_news()
    if content:
        save_news(content)
        return {"status": "success", "message": "News regenerated successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to fetch news from Coze")

# Serve Static Files (Frontend)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
