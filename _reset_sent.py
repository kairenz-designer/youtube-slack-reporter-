import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "views_data.db"
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute(
    "UPDATE reports SET sent_at = NULL WHERE video_id = ? AND report_hours = ?",
    ("j3u6I2PVHX8", 3),
)
print(f"[Reset] {c.rowcount} report(s) reset for j3u6I2PVHX8 @ 3h")
conn.commit()
conn.close()
