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
            published_at  TEXT,
            is_short      INTEGER DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS view_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id    TEXT,
            views       INTEGER,
            captured_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    try:
        c.execute("ALTER TABLE reports ADD COLUMN stats_at TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN is_short INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN slack_thread_ts TEXT")
    except Exception:
        pass
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


def add_video(video_id: str, title: str, url: str, thumbnail_url: str, published_at: str, is_short: bool = False):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO videos (video_id, title, url, thumbnail_url, published_at, is_short) VALUES (?, ?, ?, ?, ?, ?)",
        (video_id, title, url, thumbnail_url, published_at, int(is_short)),
    )
    conn.commit()
    conn.close()


def schedule_reports(video_id: str, published_at: str):
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
    c.execute("UPDATE reports SET sent_at=? WHERE id=?", (datetime.utcnow().isoformat(), report_id))
    conn.commit()
    conn.close()


# ── Snapshots (view velocity tracking) ──────────────────────────────────────

def save_snapshot(video_id: str, views: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO view_snapshots (video_id, views, captured_at) VALUES (?,?,?)",
        (video_id, views, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_recent_velocity(video_id: str, window_hours: float = 3.0) -> int | None:
    """Views gained in the last window_hours based on stored snapshots."""
    conn = get_conn()
    c = conn.cursor()
    since = (datetime.utcnow() - timedelta(hours=window_hours)).isoformat()
    c.execute(
        "SELECT views FROM view_snapshots WHERE video_id=? ORDER BY captured_at DESC LIMIT 1",
        (video_id,),
    )
    latest = c.fetchone()
    c.execute(
        "SELECT views FROM view_snapshots WHERE video_id=? AND captured_at >= ? ORDER BY captured_at ASC LIMIT 1",
        (video_id, since),
    )
    oldest = c.fetchone()
    conn.close()
    if latest and oldest:
        return latest[0] - oldest[0]
    return None


def cleanup_snapshots(keep_hours: int = 48):
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(hours=keep_hours)).isoformat()
    c.execute("DELETE FROM view_snapshots WHERE captured_at < ?", (cutoff,))
    conn.commit()
    conn.close()


# ── 24h prediction ───────────────────────────────────────────────────────────

def get_24h_prediction(video_id: str, report_hours: float, current_views: int) -> tuple | None:
    """
    Predict 24h views using historical ratio from completed videos.
    Returns (predicted_views, sample_count) or None if not enough data.
    """
    if current_views <= 0:
        return None
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT r1.views, r2.views
        FROM reports r1
        JOIN reports r2 ON r1.video_id = r2.video_id
        WHERE r1.report_hours = ? AND r1.stats_at IS NOT NULL AND r1.views > 0
          AND r2.report_hours = 24  AND r2.stats_at IS NOT NULL
          AND r1.video_id != ?
        ORDER BY r2.scheduled_at DESC
        LIMIT 15
        """,
        (report_hours, video_id),
    )
    rows = c.fetchall()
    conn.close()
    if len(rows) < 3:
        return None
    ratios = [r[1] / r[0] for r in rows if r[0] > 0]
    avg_ratio = sum(ratios) / len(ratios)
    return int(current_views * avg_ratio), len(rows)


# ── Revival detection ────────────────────────────────────────────────────────

def get_revival_candidates(days_min: int = 7, days_max: int = 90) -> list[dict]:
    """Videos old enough to check for sudden traffic spikes."""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow()
    min_pub = (now - timedelta(days=days_max)).isoformat()
    max_pub = (now - timedelta(days=days_min)).isoformat()
    c.execute(
        """
        SELECT v.video_id, v.title, v.url, v.thumbnail_url, r.views AS views_24h
        FROM videos v
        JOIN reports r ON v.video_id = r.video_id AND r.report_hours = 24 AND r.stats_at IS NOT NULL
        WHERE v.published_at BETWEEN ? AND ?
        """,
        (min_pub, max_pub),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {"video_id": r[0], "title": r[1], "url": r[2], "thumbnail_url": r[3], "views_24h": r[4]}
        for r in rows
    ]


# ── Weekly analysis ──────────────────────────────────────────────────────────

def get_videos_for_analysis(limit: int = 20) -> list[dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT v.title, r.views, r.likes, r.comments
        FROM videos v
        JOIN reports r ON v.video_id = r.video_id AND r.report_hours = 24 AND r.stats_at IS NOT NULL
        ORDER BY v.published_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    return [{"title": r[0], "views": r[1], "likes": r[2], "comments": r[3]} for r in rows]


# ── Metadata (key-value store) ───────────────────────────────────────────────

def get_metadata(key: str) -> str | None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM metadata WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def set_metadata(key: str, value: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


# ── Thread tracking ─────────────────────────────────────────────────────────

def save_thread_ts(video_id: str, thread_ts: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE videos SET slack_thread_ts=? WHERE video_id=?", (thread_ts, video_id))
    conn.commit()
    conn.close()


def get_thread_ts(video_id: str) -> str | None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT slack_thread_ts FROM videos WHERE video_id=?", (video_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_previous_report_stats(video_id: str, report_hours: float) -> dict | None:
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
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT is_short FROM videos WHERE video_id = ?", (current_video_id,))
    row = c.fetchone()
    is_short = row[0] if row else 0
    c.execute(
        """
        SELECT r.video_id, v.title, r.views, r.likes, r.comments
        FROM reports r
        JOIN videos v ON r.video_id = v.video_id
        WHERE r.report_hours = ?
          AND r.stats_at IS NOT NULL
          AND r.views IS NOT NULL
          AND r.video_id != ?
          AND v.is_short = ?
        ORDER BY v.published_at DESC
        LIMIT ?
        """,
        (report_hours, current_video_id, is_short, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {"video_id": r[0], "title": r[1], "views": r[2], "likes": r[3], "comments": r[4]}
        for r in rows
    ]
