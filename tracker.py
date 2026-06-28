"""
tracker.py — entry point for GitHub Actions.
One run cycle: write credentials → seed data → poll new videos → send due reports.
"""
import os
import sys
import json
import re
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


def _parse_duration_seconds(duration: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def get_video_is_short(video_id: str) -> bool:
    try:
        resp = _yt_client().videos().list(part="contentDetails", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return False
        duration = items[0]["contentDetails"]["duration"]
        return _parse_duration_seconds(duration) <= 180
    except Exception as e:
        print(f"[Short] check failed {video_id}: {e}", file=sys.stderr)
        return False


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
    get_benchmark_stats,
    save_snapshot, get_recent_velocity, cleanup_snapshots,
    get_revival_candidates, get_24h_prediction,
    get_videos_for_analysis, get_metadata, set_metadata,
    save_thread_ts, get_thread_ts,
    REPORT_HOURS,
)
from slack_notify import send_report, send_revival_alert, send_weekly_analysis
from ai_advisor import get_comment_summary, get_title_analysis

LOOKBACK_HOURS = 168

init_db()

# ── Helpers ──────────────────────────────────────────────────────────────────

def batch_get_video_stats(video_ids: list) -> dict:
    """Fetch stats for up to 50 videos in one API call."""
    if not video_ids:
        return {}
    resp = _yt_client().videos().list(
        part="statistics", id=",".join(video_ids[:50])
    ).execute()
    result = {}
    for item in resp.get("items", []):
        s = item["statistics"]
        result[item["id"]] = {
            "views":    int(s.get("viewCount",   0)),
            "likes":    int(s.get("likeCount",   0)),
            "comments": int(s.get("commentCount", 0)),
        }
    return result


def get_video_comments(video_id: str, max_results: int = 50) -> list[dict]:
    try:
        resp = _yt_client().commentThreads().list(
            part="snippet", videoId=video_id,
            order="relevance", maxResults=max_results,
        ).execute()
        return [
            {
                "author": i["snippet"]["topLevelComment"]["snippet"]["authorDisplayName"],
                "text":   i["snippet"]["topLevelComment"]["snippet"]["textDisplay"],
                "likes":  int(i["snippet"]["topLevelComment"]["snippet"].get("likeCount", 0)),
            }
            for i in resp.get("items", [])
        ]
    except Exception as e:
        print(f"[Comments] {video_id}: {e}", file=sys.stderr)
        return []

# ── Seed benchmark data (one-time) ──────────────────────────────────────────

def seed_benchmark_if_empty():
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
            "INSERT OR IGNORE INTO videos (video_id, title, url, thumbnail_url, published_at, is_short) "
            "VALUES (?,?,?,?,?,?)",
            (vid, "", f"https://www.youtube.com/watch?v={vid}", "", pub_str, 0),
        )
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
            is_short = get_video_is_short(video["video_id"])
            print(f"[New] {'[Short] ' if is_short else ''}{video['title']}")
            add_video(video["video_id"], video["title"], video["url"],
                      video["thumbnail_url"], video["published_at"], is_short=is_short)
        schedule_reports(video["video_id"], video["published_at"])
except Exception as e:
    print(f"[Error] fetch videos: {e}")

# ── Phase 1: Collect stats at each due mark ──────────────────────────────────

due = get_reports_needing_stats()
print(f"[Stats] {len(due)} marks due for collection")
for report in due:
    try:
        stats = get_video_stats(report["video_id"])
        if stats:
            save_stats(report["id"], stats["views"], stats["likes"], stats["comments"])
            save_snapshot(report["video_id"], stats["views"])
            print(f"[Stats] {report['report_hours']}h saved: {report['video_id']}")
    except Exception as e:
        print(f"[Error] stats {report['video_id']}: {e}")

# ── Phase 2: Send Slack reports using stored stats ───────────────────────────

to_send = get_reports_ready_to_send()
print(f"[Reports] {len(to_send)} ready to send")
for report in to_send:
    try:
        stats = {"views": report["views"], "likes": report["likes"], "comments": report["comments"]}
        benchmark = get_benchmark_stats(report["video_id"], report["report_hours"], limit=10)
        video = {
            "video_id":      report["video_id"],
            "title":         report["title"],
            "url":           report["url"],
            "thumbnail_url": report["thumbnail_url"],
        }

        # 24h prediction (chỉ cho mốc < 24h)
        predicted_24h = None
        if report["report_hours"] < 24:
            predicted_24h = get_24h_prediction(
                report["video_id"], report["report_hours"], report["views"]
            )

        # Comment summary (chỉ cho mốc 24h)
        comment_summary = None
        if report["report_hours"] == 24:
            comments = get_video_comments(report["video_id"])
            if comments:
                comment_summary = get_comment_summary(report["title"], comments)

        thread_ts = get_thread_ts(report["video_id"])
        is_anchor = (report["report_hours"] == 1) or (thread_ts is None)

        msg_ts = send_report(video, stats, report["report_hours"], benchmark,
                    predicted_24h=predicted_24h, comment_summary=comment_summary,
                    thread_ts=None if is_anchor else thread_ts)
        mark_slack_sent(report["id"])

        if is_anchor and msg_ts:
            save_thread_ts(report["video_id"], msg_ts)
    except Exception as e:
        print(f"[Error] send {report['video_id']}: {e}")

# ── Revival check: batch poll old videos for sudden spikes ───────────────────

revival_candidates = get_revival_candidates()
if revival_candidates:
    current = batch_get_video_stats([v["video_id"] for v in revival_candidates])
    for video in revival_candidates:
        curr = current.get(video["video_id"])
        if not curr:
            continue
        save_snapshot(video["video_id"], curr["views"])
        velocity_3h = get_recent_velocity(video["video_id"], window_hours=3)
        if velocity_3h is None or video["views_24h"] <= 0:
            continue
        baseline_3h = int(video["views_24h"] / 24 * 3)  # expected views in 3h at day-1 rate
        if baseline_3h > 0 and velocity_3h > baseline_3h * 4:
            print(f"[Revival] {video['video_id']}: +{velocity_3h} in 3h vs baseline {baseline_3h}")
            try:
                send_revival_alert(video, curr["views"], velocity_3h, baseline_3h)
            except Exception as e:
                print(f"[Error] revival alert {video['video_id']}: {e}")

# ── Weekly title analysis (mỗi thứ 2, chạy 1 lần/tuần) ─────────────────────

now = datetime.utcnow()
if now.weekday() == 0:  # Monday
    last_run = get_metadata("last_weekly_analysis")
    should_run = True
    if last_run:
        try:
            days_ago = (now - datetime.fromisoformat(last_run)).days
            if days_ago < 6:
                should_run = False
        except Exception:
            pass
    if should_run:
        analysis_videos = get_videos_for_analysis(limit=20)
        if len(analysis_videos) >= 5:
            print(f"[Weekly] Running title analysis on {len(analysis_videos)} videos")
            analysis = get_title_analysis(analysis_videos)
            if analysis:
                try:
                    send_weekly_analysis(analysis, len(analysis_videos))
                    set_metadata("last_weekly_analysis", now.isoformat())
                except Exception as e:
                    print(f"[Error] weekly analysis: {e}")

# ── Cleanup ───────────────────────────────────────────────────────────────────

cleanup_snapshots(keep_hours=48)

print("[Done]")
