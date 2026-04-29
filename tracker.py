"""
tracker.py — entry point for GitHub Actions.
One run cycle: write credentials → seed data → poll new videos → send due reports.
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Write credential files injected by GitHub Actions secrets
_base = Path(__file__).parent
for _env_var, _filename in [
    ("GOOGLE_CLIENT_SECRET_JSON", "client_secret.json"),
    ("GOOGLE_TOKEN_JSON", "token.json"),
]:
    _val = os.environ.get(_env_var)
    if _val:
        (_base / _filename).write_text(_val)

# ── YouTube Data API ────────────────────────────────────────────────────────

from googleapiclient.discovery import build as _yt_build

_YOUTUBE_API_KEY    = os.environ["YOUTUBE_API_KEY"]
_YOUTUBE_CHANNEL_ID = os.environ["YOUTUBE_CHANNEL_ID"]
_UPLOADS_PLAYLIST   = "UU" + _YOUTUBE_CHANNEL_ID[2:]


def _yt_client():
    return _yt_build("youtube", "v3", developerKey=_YOUTUBE_API_KEY)


def get_recent_videos(published_after: datetime) -> list[dict]:
    """Return videos published after `published_after` (UTC-aware)."""
    yt = _yt_client()
    if published_after.tzinfo is None:
        published_after = published_after.replace(tzinfo=timezone.utc)

    resp = yt.playlistItems().list(
        part="snippet", playlistId=_UPLOADS_PLAYLIST, maxResults=15
    ).execute()

    videos = []
    for item in resp.get("items", []):
        snippet  = item["snippet"]
        resource = snippet.get("resourceId", {})
        if resource.get("kind") != "youtube#video":
            continue
        pub_str = snippet.get("publishedAt", "")
        if not pub_str:
            continue
        pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        if pub >= published_after:
            video_id = resource["videoId"]
            videos.append({
                "video_id":      video_id,
                "title":         snippet.get("title", ""),
                "url":           f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "published_at":  pub_str,
            })
    return videos


def get_video_stats(video_id: str) -> dict | None:
    """Fetch current viewCount, likeCount, commentCount."""
    resp  = _yt_client().videos().list(part="statistics", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        return None
    s = items[0]["statistics"]
    return {
        "views":    int(s.get("viewCount",   0)),
        "likes":    int(s.get("likeCount",   0)),
        "comments": int(s.get("commentCount", 0)),
    }


# ── YouTube Analytics API ───────────────────────────────────────────────────

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build as _analytics_build

_ANALYTICS_SCOPES      = ["https://www.googleapis.com/auth/yt-analytics.readonly"]
_CLIENT_SECRET_FILE    = _base / "client_secret.json"
_TOKEN_FILE            = _base / "token.json"
_YOUTUBE_OWNER_EMAIL   = os.environ.get("YOUTUBE_OWNER_EMAIL", "")


def _analytics_credentials() -> Credentials:
    creds = None
    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _ANALYTICS_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CLIENT_SECRET_FILE), _ANALYTICS_SCOPES
            )
            kwargs = {"login_hint": _YOUTUBE_OWNER_EMAIL} if _YOUTUBE_OWNER_EMAIL else {}
            creds = flow.run_local_server(port=0, **kwargs)
        _TOKEN_FILE.write_text(creds.to_json())
    return creds


def get_views_first_day(video_id: str, published_at: str) -> int | None:
    """
    Return total views on the publish day via Analytics API.
    Day-level is the max granularity available — used as approximate 24h seed.
    Only the channel OWNER (phoenix@eoeoeo.net) has access.
    """
    try:
        yt_analytics = _analytics_build("youtubeAnalytics", "v2",
                                        credentials=_analytics_credentials())
        pub        = datetime.fromisoformat(published_at.replace("Z", "+00:00")).replace(tzinfo=None)
        start_date = pub.strftime("%Y-%m-%d")
        end_date   = (pub + timedelta(days=1)).strftime("%Y-%m-%d")
        resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views",
            dimensions="day",
            filters=f"video=={video_id}",
        ).execute()
        rows  = resp.get("rows", [])
        total = sum(int(r[1]) for r in rows)
        return total if total > 0 else None
    except Exception as e:
        print(f"[Analytics] {video_id}: {e}", file=sys.stderr)
        return None


# ── Main run logic ──────────────────────────────────────────────────────────

from db import (
    init_db, is_video_known, add_video, schedule_reports,
    get_reports_needing_stats, save_stats,
    get_reports_ready_to_send, mark_slack_sent,
    get_previous_report_stats, get_benchmark_stats,
    REPORT_HOURS,
)
from slack_notify import send_report

LOOKBACK_HOURS = 168

init_db()

# ── Seed benchmark data (one-time) ──────────────────────────────────────────

def seed_benchmark_if_empty():
    """Seed historical 3h data from YouTube Studio so benchmark works on day one."""
    import sqlite3
    from db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM reports WHERE report_hours = 3 AND stats_at IS NOT NULL")
    if c.fetchone()[0] >= 3:
        conn.close()
        return
    print("[Seed] Seeding 3h benchmark data...")
    studio_data = [
        ("J6Kc8Y3umwI", "2026-04-07T04:03:48", 1800),
        ("0wxJFK_PYJ8", "2026-03-30T03:31:15",  977),
        ("K46m2FHD3YU", "2026-03-20T06:22:04",  563),
        ("SLHHxh1wSG0", "2026-03-25T05:19:50",  236),
        ("Rj6BCFb7_uI", "2026-03-16T04:59:22",  160),
        ("Jx-VNBYidNk", "2026-03-27T13:01:19",  154),
        ("VYcGgZ1sqZY", "2026-03-27T05:34:11",   87),
        ("giE9o1ZxrnQ", "2026-03-26T04:12:22",   73),
        ("bANMLnTtHus", "2026-03-25T09:50:34",   55),
    ]
    ts = datetime.utcnow().isoformat()
    for vid, pub_str, views_3h in studio_data:
        pub = datetime.fromisoformat(pub_str)
        scheduled = (pub + timedelta(hours=3)).isoformat()
        c.execute(
            "INSERT OR IGNORE INTO reports "
            "(video_id, report_hours, scheduled_at, stats_at, sent_at, views, likes, comments) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (vid, 3, scheduled, ts, ts, views_3h, 0, 0),
        )
    conn.commit()
    conn.close()
    print("[Seed] Done.")

seed_benchmark_if_empty()

# ── Poll for new videos ──────────────────────────────────────────────────────

published_after = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
try:
    videos = get_recent_videos(published_after)
    for video in videos:
        if not is_video_known(video["video_id"]):
            print(f"[New] {video['title']}")
            add_video(video["video_id"], video["title"], video["url"],
                      video["thumbnail_url"], video["published_at"])
        schedule_reports(video["video_id"], video["published_at"])
except Exception as e:
    print(f"[Error] fetch videos: {e}")

# ── Phase 1: Collect stats at each due mark (always, regardless of Slack) ───

due = get_reports_needing_stats()
print(f"[Stats] {len(due)} marks due for collection")
for report in due:
    try:
        stats = get_video_stats(report["video_id"])
        if stats:
            save_stats(report["id"], stats["views"], stats["likes"], stats["comments"])
            print(f"[Stats] {report['report_hours']}h saved: {report['video_id']}")
    except Exception as e:
        print(f"[Error] stats {report['video_id']}: {e}")

# ── Phase 2: Send Slack report using stored stats ────────────────────────────

to_send = get_reports_ready_to_send()
print(f"[Reports] {len(to_send)} ready to send")
for report in to_send:
    try:
        stats = {"views": report["views"], "likes": report["likes"], "comments": report["comments"]}
        previous_stats = get_previous_report_stats(report["video_id"], report["report_hours"])
        benchmark      = get_benchmark_stats(report["video_id"], report["report_hours"], limit=10)
        video = {
            "video_id":      report["video_id"],
            "title":         report["title"],
            "url":           report["url"],
            "thumbnail_url": report["thumbnail_url"],
        }
        send_report(video, stats, report["report_hours"], previous_stats, benchmark)
        mark_slack_sent(report["id"])
    except Exception as e:
        print(f"[Error] send {report['video_id']}: {e}")

print("[Done]")
