"""
comment_monitor.py — Phát hiện comment mới trên các video gần đây và notify Slack.
Chạy độc lập mỗi 5 phút, không liên quan đến vòng lặp report thông thường.
"""
import os
import sys
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from googleapiclient.discovery import build as _yt_build
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from db import get_conn, init_db

_YT_KEY       = os.environ["YOUTUBE_API_KEY"]
_SLACK_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
_SLACK_CH     = os.environ["SLACK_CHANNEL"]
_MONITOR_DAYS = 7   # chỉ theo dõi video mới trong 7 ngày gần nhất
_MAX_COMMENTS = 20  # số comment fetch mỗi lần


def _yt():
    return _yt_build("youtube", "v3", developerKey=_YT_KEY)


def _recent_videos() -> list[dict]:
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(days=_MONITOR_DAYS)).isoformat()
    c.execute(
        "SELECT video_id, title, url, thumbnail_url FROM videos WHERE published_at >= ? ORDER BY published_at DESC",
        (cutoff,),
    )
    rows = c.fetchall()
    conn.close()
    return [{"video_id": r[0], "title": r[1], "url": r[2], "thumbnail_url": r[3]} for r in rows]


def _seen_ids(video_id: str) -> set:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT comment_id FROM seen_comments WHERE video_id = ?", (video_id,))
    ids = {r[0] for r in c.fetchall()}
    conn.close()
    return ids


def _mark_seen(video_id: str, comment_ids: list):
    if not comment_ids:
        return
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.executemany(
        "INSERT OR IGNORE INTO seen_comments (comment_id, video_id, seen_at) VALUES (?,?,?)",
        [(cid, video_id, now) for cid in comment_ids],
    )
    conn.commit()
    conn.close()


def _fetch_comments(video_id: str) -> list[dict]:
    try:
        resp = _yt().commentThreads().list(
            part="snippet",
            videoId=video_id,
            order="time",
            maxResults=_MAX_COMMENTS,
        ).execute()
    except Exception as e:
        print(f"[Comments] Lỗi fetch {video_id}: {e}", file=sys.stderr)
        return []

    out = []
    for item in resp.get("items", []):
        snip = item["snippet"]["topLevelComment"]["snippet"]
        out.append({
            "id":     item["id"],
            "author": snip["authorDisplayName"],
            "text":   snip["textDisplay"],
            "likes":  int(snip.get("likeCount", 0)),
        })
    return out


def _send_slack(video: dict, comments: list[dict]):
    client = WebClient(token=_SLACK_TOKEN)
    n = len(comments)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"💬 {n} bình luận mới", "emoji": True},
        },
        {
            "type": "image",
            "image_url": video["thumbnail_url"],
            "alt_text": video["title"],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{video['url']}|{video['title']}>*",
            },
        },
        {"type": "divider"},
    ]

    for cm in comments[:5]:
        text  = cm["text"][:400].replace("\n", " ")
        likes = f"  ·  ❤️ {cm['likes']}" if cm["likes"] > 0 else ""
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{cm['author']}*{likes}\n>{text}",
            },
        })

    if n > 5:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_... và {n - 5} bình luận khác_"}],
        })

    try:
        client.chat_postMessage(
            channel=_SLACK_CH,
            blocks=blocks,
            text=f"{n} bình luận mới trên: {video['title']}",
            unfurl_links=False,
            unfurl_media=False,
        )
        print(f"[Comments] Gửi {n} comment mới — {video['video_id']}")
    except SlackApiError as e:
        print(f"[Comments] Slack lỗi: {e.response['error']}", file=sys.stderr)


def run():
    init_db()
    videos = _recent_videos()
    print(f"[Comments] Theo dõi {len(videos)} video")

    for video in videos:
        comments = _fetch_comments(video["video_id"])
        if not comments:
            continue

        seen    = _seen_ids(video["video_id"])
        all_ids = [c["id"] for c in comments]

        if not seen:
            # Lần đầu thấy video này — seed im lặng, không spam Slack
            _mark_seen(video["video_id"], all_ids)
            print(f"[Comments] Seed {len(all_ids)} comment có sẵn — {video['video_id']}")
            continue

        new = [c for c in comments if c["id"] not in seen]
        _mark_seen(video["video_id"], all_ids)

        if new:
            _send_slack(video, new)


if __name__ == "__main__":
    run()
