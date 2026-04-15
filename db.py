import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "views_data.db"


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
            report_hours  INTEGER,
            scheduled_at  TEXT,
            sent_at       TEXT,
            views         INTEGER,
            likes         INTEGER,
            comments      INTEGER,
            ctr           REAL,
            UNIQUE(video_id, report_hours)
        )
    """)
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
    """Schedule 1h, 2h, 3h, 8h, 12h, 24h, 72h (3 days), 168h (1 week) reports.
    Allow up to 30 minutes grace period so cron delays don't cause missed reports.
    """
    conn = get_conn()
    c = conn.cursor()
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00")).replace(tzinfo=None)
    now = datetime.utcnow()
    grace = timedelta(minutes=30)

    for hours in [1, 2, 3, 8, 12, 24, 72, 168]:
        scheduled = pub + timedelta(hours=hours)
        if scheduled > now - grace:  # allow up to 30 min late
            c.execute(
                "INSERT OR IGNORE INTO reports (video_id, report_hours, scheduled_at) VALUES (?, ?, ?)",
                (video_id, hours, scheduled.isoformat()),
            )
    conn.commit()
    conn.close()


def get_pending_reports() -> list[dict]:
    """Return reports whose scheduled time has passed but haven't been sent yet."""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute(
        """
        SELECT r.id, r.video_id, r.report_hours, v.title, v.url, v.thumbnail_url, v.published_at
        FROM reports r
        JOIN videos v ON r.video_id = v.video_id
        WHERE r.sent_at IS NULL AND r.scheduled_at <= ?
        ORDER BY r.scheduled_at
        """,
        (now,),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "video_id": r[1],
            "report_hours": r[2],
            "title": r[3],
            "url": r[4],
            "thumbnail_url": r[5],
            "published_at": r[6],
        }
        for r in rows
    ]


def mark_report_sent(report_id: int, views: int, likes: int, comments: int, ctr: float | None = None):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE reports SET sent_at = ?, views = ?, likes = ?, comments = ?, ctr = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), views, likes, comments, ctr, report_id),
    )
    conn.commit()
    conn.close()


def get_previous_report_stats(video_id: str, report_hours: int) -> dict | None:
    """Return stats from the most recent report sent before this one."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT views, likes, comments, ctr FROM reports
        WHERE video_id = ? AND report_hours < ? AND sent_at IS NOT NULL
        ORDER BY report_hours DESC LIMIT 1
        """,
        (video_id, report_hours),
    )
    row = c.fetchone()
    conn.close()
    if row:
        return {"views": row[0], "likes": row[1], "comments": row[2], "ctr": row[3]}
    return None


def get_benchmark_stats(current_video_id: str, report_hours: int, limit: int = 10) -> list[dict]:
    """
    Return stats of the last `limit` OTHER videos at the same report_hours.
    Used to rank the current video against recent channel history.
    Only includes videos that have actually had their report sent (data exists).
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT r.video_id, v.title, r.views, r.likes, r.comments
        FROM reports r
        JOIN videos v ON r.video_id = v.video_id
        WHERE r.report_hours = ?
          AND r.sent_at IS NOT NULL
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
