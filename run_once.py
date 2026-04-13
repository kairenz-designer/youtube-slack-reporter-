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
from analytics import get_video_ctr
from slack_reporter import send_report
from datetime import datetime, timedelta, timezone

LOOKBACK_HOURS = 24

init_db()

# Check for new videos
published_after = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
try:
    videos = get_recent_videos(published_after)
    for video in videos:
        if not is_video_known(video["video_id"]):
            print(f"[New] {video['title']}")
            add_video(video["video_id"], video["title"], video["url"], video["thumbnail_url"], video["published_at"])
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
        ctr_data       = get_video_ctr(report["video_id"], report["published_at"])
        ctr            = ctr_data["ctr"] if ctr_data else None
        previous_ctr   = previous_stats.get("ctr") if previous_stats else None

        video = {"video_id": report["video_id"], "title": report["title"], "url": report["url"], "thumbnail_url": report["thumbnail_url"]}

        send_report(video, stats, report["report_hours"], previous_stats, ctr, previous_ctr, benchmark)
        mark_report_sent(report["id"], stats["views"], stats["likes"], stats["comments"], ctr=ctr)

    except Exception as e:
        print(f"[Error] report {report['video_id']}: {e}")

print("[Done]")
