"""
One-time script to backfill accurate benchmark data from YouTube Analytics API.
Run this ONCE after OAuth login is complete (token.json exists).

Usage: python backfill_benchmark.py
"""
import os, sys, sqlite3
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

from db import init_db, DB_PATH
from youtube import get_recent_videos
from analytics import backfill_benchmark_from_analytics

init_db()

print("Fetching last 30 days of videos...")
published_after = datetime.now(timezone.utc) - timedelta(days=30)
videos = get_recent_videos(published_after)
print(f"Found {len(videos)} videos\n")

print("Fetching hourly view data from YouTube Analytics...")
data = backfill_benchmark_from_analytics(videos, hours_list=[1, 3, 6, 24])

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
inserted = 0

for v in videos:
    vid = v["video_id"]
    pub = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00")).replace(tzinfo=None)

    c.execute(
        "INSERT OR IGNORE INTO videos (video_id, title, url, thumbnail_url, published_at) VALUES (?,?,?,?,?)",
        (vid, v["title"], v["url"], v["thumbnail_url"], v["published_at"])
    )

    for hours, views in data.get(vid, {}).items():
        scheduled = pub + timedelta(hours=hours)
        c.execute(
            """INSERT OR REPLACE INTO reports
               (video_id, report_hours, scheduled_at, sent_at, views, likes, comments)
               VALUES (?,?,?,?,?,?,?)""",
            (vid, hours, scheduled.isoformat(), scheduled.isoformat(), views, 0, 0)
        )
        inserted += 1

conn.commit()
conn.close()

print(f"\nDone — {inserted} accurate benchmark entries inserted.")
print("Benchmark data is now ready for all time marks (1h, 3h, 6h, 24h).")
