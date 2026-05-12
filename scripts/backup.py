import shutil
import sqlite3
from datetime import date
from pathlib import Path

BACKUP_DIR = Path("backups")
DB_PATH = Path("news.db")


def backup():
    BACKUP_DIR.mkdir(exist_ok=True)
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    today = date.today().isoformat()
    dest = BACKUP_DIR / f"news_{today}.db"
    shutil.copy2(DB_PATH, dest)
    print(f"Backup: {DB_PATH} -> {dest}")


if __name__ == "__main__":
    backup()
