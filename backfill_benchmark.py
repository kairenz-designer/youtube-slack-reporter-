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
from analytics import get_views_first_day

init_db()

print("Fetching last 30 days of videos...")
published_after = datetime.now(timezone.utc) - timedelta(days=30)
videos = get_recent_videos(published_after)
print(f"Found {len(videos)} videos\n")

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
inserted = 0

for v in videos:
    vid = v["video_id"]
    pub = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    age_hours = (datetime.utcnow() - pub).total_seconds() / 3600

    if age_hours < 24:
        print(f"  Skip (too new): {v['title'][:50]}")
        continue

    c.execute(
        "INSERT OR IGNORE INTO videos (video_id, title, url, thumbnail_url, published_at) VALUES (?,?,?,?,?)",
        (vid, v["title"], v["url"], v["thumbnail_url"], v["published_at"])
    )

    # YouTube Analytics API only supports day-level granularity
    # Use day-1 views as approximate 24h benchmark seed
    views = get_views_first_day(vid, v["published_at"])
    if views:
        scheduled = pub + timedelta(hours=24)
        c.execute(
            """INSERT OR REPLACE INTO reports
               (video_id, report_hours, scheduled_at, sent_at, views, likes, comments)
               VALUES (?,?,?,?,?,?,?)""",
            (vid, 24, scheduled.isoformat(), scheduled.isoformat(), views, 0, 0)
        )
        inserted += 1
        print(f"  24h seed: {v['title'][:50]} — {views:,} views")
    else:
        print(f"  No data: {v['title'][:50]}")

conn.commit()
conn.close()

print(f"\nDone — {inserted} entries seeded for 24h benchmark.")
print("Note: 1h/2h/3h/8h/12h/72h/168h benchmarks will accumulate naturally from real reports.")
