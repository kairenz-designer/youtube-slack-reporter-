import os
import sys
import time
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 output so Vietnamese characters don't crash on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _write_credential_files():
    """Write credential files from environment variables (used on Railway)."""
    base = Path(__file__).parent

    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET_JSON")
    if client_secret and not (base / "client_secret.json").exists():
        (base / "client_secret.json").write_text(client_secret)
        print("[Init] Wrote client_secret.json from env")

    token = os.environ.get("GOOGLE_TOKEN_JSON")
    if token and not (base / "token.json").exists():
        (base / "token.json").write_text(token)
        print("[Init] Wrote token.json from env")


_write_credential_files()

from db import (
    init_db,
    is_video_known,
    add_video,
    schedule_reports,
    get_pending_reports,
    mark_report_sent,
    get_previous_report_stats,
    get_benchmark_stats,
)
from youtube import get_recent_videos, get_video_stats
from analytics import get_video_ctr
from slack_reporter import send_report

POLL_INTERVAL = 5 * 60  # 5 minutes
LOOKBACK_HOURS = 24


def check_new_videos():
    published_after = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    try:
        videos = get_recent_videos(published_after)
    except Exception as e:
        print(f"[YouTube] Failed to fetch videos: {e}")
        return

    for video in videos:
        if not is_video_known(video["video_id"]):
            print(f"[YouTube] New video: {video['title']}")
            add_video(
                video["video_id"],
                video["title"],
                video["url"],
                video["thumbnail_url"],
                video["published_at"],
            )
            schedule_reports(video["video_id"], video["published_at"])


def process_pending_reports():
    pending = get_pending_reports()
    if pending:
        print(f"[Scheduler] {len(pending)} report(s) due")

    for report in pending:
        try:
            stats = get_video_stats(report["video_id"])
        except Exception as e:
            print(f"[YouTube] Failed to fetch stats for {report['video_id']}: {e}")
            continue

        if stats is None:
            print(f"[YouTube] No stats for {report['video_id']} — skipping")
            continue

        previous_stats = get_previous_report_stats(report["video_id"], report["report_hours"])
        benchmark      = get_benchmark_stats(report["video_id"], report["report_hours"], limit=10)
        ctr_data       = get_video_ctr(report["video_id"], report["published_at"])
        ctr            = ctr_data["ctr"] if ctr_data else None
        previous_ctr   = previous_stats.get("ctr") if previous_stats else None

        video = {
            "video_id": report["video_id"],
            "title": report["title"],
            "url": report["url"],
            "thumbnail_url": report["thumbnail_url"],
        }

        try:
            send_report(video, stats, report["report_hours"], previous_stats, ctr, previous_ctr, benchmark)
            mark_report_sent(
                report["id"],
                stats["views"],
                stats["likes"],
                stats["comments"],
                ctr=ctr,
            )
        except Exception as e:
            print(f"[Slack] Failed to send report: {e}")


def main():
    print("=" * 50)
    print("  YouTube -> Slack Reporter")
    print(f"  Polling every {POLL_INTERVAL // 60} minutes")
    print("  Reports at: +1h, +3h, +6h, +24h after publish")
    print("=" * 50)

    init_db()

    while True:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{ts}] Polling...")
        check_new_videos()
        process_pending_reports()
        print(f"[{ts}] Sleeping {POLL_INTERVAL // 60} min...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
