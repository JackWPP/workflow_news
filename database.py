import sqlite3
from datetime import datetime
import os

DB_PATH = "news.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create news table
    # content: The markdown content
    # date: YYYY-MM-DD
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def save_news(content: str, date: str = None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO news (date, content) VALUES (?, ?)",
        (date, content)
    )
    conn.commit()
    conn.close()

def get_latest_news_by_date(date: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Get the most recent generation for a specific date
    cursor.execute(
        "SELECT * FROM news WHERE date = ? ORDER BY id DESC LIMIT 1",
        (date,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_history_dates():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Get unique dates that have news, ordered by date desc
    cursor.execute(
        "SELECT DISTINCT date FROM news ORDER BY date DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [row['date'] for row in rows]

def get_today_news():
    today = datetime.now().strftime("%Y-%m-%d")
    return get_latest_news_by_date(today)
