import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "views_data.db"

REPORT_HOURS = [1, 3, 6, 24]


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id      TEXT PRIMARY KEY,
            title         TEXT,
            url           TEXT,
            thumbnail_url TEXT,
            published_at  TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id      TEXT,
            report_hours  REAL,
            scheduled_at  TEXT,
            stats_at      TEXT,
            sent_at       TEXT,
            views         INTEGER,
            likes         INTEGER,
            comments      INTEGER,
            ctr           REAL,
            UNIQUE(video_id, report_hours)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_comments (
            comment_id TEXT PRIMARY KEY,
            video_id   TEXT,
            seen_at    TEXT
        )
    """)
    # Migration: add stats_at for existing DBs that don't have it yet
    try:
        c.execute("ALTER TABLE reports ADD COLUMN stats_at TEXT")
    except Exception:
        pass
    # Backfill: old records where stats were saved alongside Slack send
    c.execute(
        "UPDATE reports SET stats_at = sent_at WHERE sent_at IS NOT NULL AND stats_at IS NULL"
    )
    conn.commit()
    conn.close()


def is_video_known(video_id: str) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,))
    result = c.fetchone()
    conn.close()
    return result is not None


def add_video(video_id: str, title: str, url: str, thumbnail_url: str, published_at: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO videos (video_id, title, url, thumbnail_url, published_at) VALUES (?, ?, ?, ?, ?)",
        (video_id, title, url, thumbnail_url, published_at),
    )
    conn.commit()
    conn.close()


def schedule_reports(video_id: str, published_at: str):
    """Schedule reports at 1h, 3h, 6h, 24h after publish.
    Allow up to 30 minutes grace period so cron delays don't cause missed reports.
    """
    conn = get_conn()
    c = conn.cursor()
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00")).replace(tzinfo=None)
    now = datetime.utcnow()
    grace = timedelta(minutes=30)

    for hours in REPORT_HOURS:
        scheduled = pub + timedelta(hours=hours)
        if scheduled > now - grace:
            c.execute(
                "INSERT OR IGNORE INTO reports (video_id, report_hours, scheduled_at) VALUES (?, ?, ?)",
                (video_id, hours, scheduled.isoformat()),
            )
    conn.commit()
    conn.close()


# ── Phase 1: collect stats ───────────────────────────────────────────────────

def get_reports_needing_stats() -> list[dict]:
    """Reports whose scheduled time has passed but stats haven't been fetched yet."""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute(
        """
        SELECT r.id, r.video_id, r.report_hours, v.title, v.url, v.thumbnail_url, v.published_at
        FROM reports r
        JOIN videos v ON r.video_id = v.video_id
        WHERE r.scheduled_at <= ? AND r.stats_at IS NULL
        ORDER BY r.scheduled_at
        """,
        (now,),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "video_id": r[1], "report_hours": r[2],
            "title": r[3], "url": r[4], "thumbnail_url": r[5], "published_at": r[6],
        }
        for r in rows
    ]


def save_stats(report_id: int, views: int, likes: int, comments: int):
    """Store YouTube stats for a report mark. Called as soon as data is fetched."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE reports SET stats_at=?, views=?, likes=?, comments=? WHERE id=?",
        (datetime.utcnow().isoformat(), views, likes, comments, report_id),
    )
    conn.commit()
    conn.close()


# ── Phase 2: send Slack ──────────────────────────────────────────────────────

def get_reports_ready_to_send() -> list[dict]:
    """Reports with stats collected but Slack not sent yet."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT r.id, r.video_id, r.report_hours, r.views, r.likes, r.comments,
               v.title, v.url, v.thumbnail_url, v.published_at
        FROM reports r
        JOIN videos v ON r.video_id = v.video_id
        WHERE r.stats_at IS NOT NULL AND r.sent_at IS NULL
        ORDER BY r.scheduled_at
        """,
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "video_id": r[1], "report_hours": r[2],
            "views": r[3], "likes": r[4], "comments": r[5],
            "title": r[6], "url": r[7], "thumbnail_url": r[8], "published_at": r[9],
        }
        for r in rows
    ]


def mark_slack_sent(report_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE reports SET sent_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), report_id),
    )
    conn.commit()
    conn.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_previous_report_stats(video_id: str, report_hours: float) -> dict | None:
    """Return stats from the most recent earlier mark that already has data."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT views, likes, comments FROM reports
        WHERE video_id = ? AND report_hours < ? AND stats_at IS NOT NULL
        ORDER BY report_hours DESC LIMIT 1
        """,
        (video_id, report_hours),
    )
    row = c.fetchone()
    conn.close()
    if row:
        return {"views": row[0], "likes": row[1], "comments": row[2]}
    return None


def get_benchmark_stats(current_video_id: str, report_hours: float, limit: int = 10) -> list[dict]:
    """Stats of the last `limit` OTHER videos at the same time mark (data collected)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT r.video_id, v.title, r.views, r.likes, r.comments
        FROM reports r
        JOIN videos v ON r.video_id = v.video_id
        WHERE r.report_hours = ?
          AND r.stats_at IS NOT NULL
          AND r.views IS NOT NULL
          AND r.video_id != ?
        ORDER BY v.published_at DESC
        LIMIT ?
        """,
        (report_hours, current_video_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {"video_id": r[0], "title": r[1], "views": r[2], "likes": r[3], "comments": r[4]}
        for r in rows
    ]
