"""
Single execution script for GitHub Actions.
Runs one poll cycle: check for new videos + send any due reports.
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Write credential files from environment variables (injected by GitHub Actions secrets)
base = Path(__file__).parent

client_secret = os.environ.get("GOOGLE_CLIENT_SECRET_JSON")
if client_secret:
    (base / "client_secret.json").write_text(client_secret)

token = os.environ.get("GOOGLE_TOKEN_JSON")
if token:
    (base / "token.json").write_text(token)

from db import init_db, is_video_known, add_video, schedule_reports, get_pending_reports, mark_report_sent, get_previous_report_stats, get_benchmark_stats
from youtube import get_recent_videos, get_video_stats
from slack_reporter import send_report
from datetime import datetime, timedelta, timezone

LOOKBACK_HOURS = 24

init_db()


def seed_3h_benchmark_if_empty():
    """
    One-time seed of accurate 3h benchmark data captured from YouTube Studio.
    These are the exact first-3h views for each video, not approximations.
    Runs only if fewer than 3 historical 3h entries exist.
    """
    import sqlite3
    from db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM reports WHERE report_hours = 3 AND sent_at IS NOT NULL")
    count = c.fetchone()[0]
    conn.close()

    if count >= 3:
        return  # already has enough 3h data

    print("[Seed3h] Seeding 3h benchmark from YouTube Studio data...")
    # Data captured from YouTube Studio screenshot on 2026-04-14
    # Format: (video_id, published_at_utc, views_at_3h)
    studio_data = [
        ("J6Kc8Y3umwI", "2026-04-07T04:03:48", 1800),
        ("0wxJFK_PYJ8", "2026-03-30T03:31:15", 977),
        ("K46m2FHD3YU", "2026-03-20T06:22:04", 563),
        ("SLHHxh1wSG0", "2026-03-25T05:19:50", 236),
        ("Rj6BCFb7_uI", "2026-03-16T04:59:22", 160),
        ("Jx-VNBYidNk", "2026-03-27T13:01:19", 154),
        ("VYcGgZ1sqZY", "2026-03-27T05:34:11", 87),
        ("giE9o1ZxrnQ", "2026-03-26T04:12:22", 73),
        ("bANMLnTtHus", "2026-03-25T09:50:34", 55),
    ]

    from datetime import datetime, timedelta
    import sqlite3
    from db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    seeded = 0
    for vid, pub_str, views_3h in studio_data:
        pub = datetime.fromisoformat(pub_str)
        scheduled = (pub + timedelta(hours=3)).isoformat()
        c.execute(
            """INSERT OR IGNORE INTO reports
               (video_id, report_hours, scheduled_at, sent_at, views, likes, comments)
               VALUES (?,?,?,?,?,?,?)""",
            (vid, 3, scheduled, scheduled, views_3h, 0, 0)
        )
        seeded += 1
    conn.commit()
    conn.close()
    print(f"[Seed3h] Done — {seeded} entries seeded.")


seed_3h_benchmark_if_empty()

# Check for new videos
published_after = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
try:
    videos = get_recent_videos(published_after)
    for video in videos:
        if not is_video_known(video["video_id"]):
            print(f"[New] {video['title']}")
            add_video(video["video_id"], video["title"], video["url"], video["thumbnail_url"], video["published_at"])
        # Always re-check scheduling — catches missed slots if cron was delayed
        schedule_reports(video["video_id"], video["published_at"])
except Exception as e:
    print(f"[Error] fetch videos: {e}")

# Send pending reports
pending = get_pending_reports()
print(f"[Reports] {len(pending)} due")

for report in pending:
    try:
        stats = get_video_stats(report["video_id"])
        if not stats:
            continue

        previous_stats = get_previous_report_stats(report["video_id"], report["report_hours"])
        benchmark      = get_benchmark_stats(report["video_id"], report["report_hours"], limit=10)

        video = {"video_id": report["video_id"], "title": report["title"], "url": report["url"], "thumbnail_url": report["thumbnail_url"]}

        send_report(video, stats, report["report_hours"], previous_stats, benchmark)
        mark_report_sent(report["id"], stats["views"], stats["likes"], stats["comments"])

    except Exception as e:
        print(f"[Error] report {report['video_id']}: {e}")

print("[Done]")
