import os
from datetime import datetime, timezone
from googleapiclient.discovery import build

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
YOUTUBE_CHANNEL_ID = os.environ["YOUTUBE_CHANNEL_ID"]

# Uploads playlist ID is derived from channel ID: UC... → UU...
UPLOADS_PLAYLIST_ID = "UU" + YOUTUBE_CHANNEL_ID[2:]


def _client():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def get_recent_videos(published_after: datetime) -> list[dict]:
    """
    Return videos published after `published_after` (UTC-aware datetime).
    Uses playlistItems.list (1 quota unit) instead of search.list (100 units).
    """
    youtube = _client()

    if published_after.tzinfo is None:
        published_after = published_after.replace(tzinfo=timezone.utc)

    response = (
        youtube.playlistItems()
        .list(
            part="snippet",
            playlistId=UPLOADS_PLAYLIST_ID,
            maxResults=15,
        )
        .execute()
    )

    videos = []
    for item in response.get("items", []):
        snippet = item["snippet"]
        resource = snippet.get("resourceId", {})
        if resource.get("kind") != "youtube#video":
            continue

        video_id = resource["videoId"]
        title = snippet.get("title", "")
        published_at_str = snippet.get("publishedAt", "")

        if not published_at_str:
            continue

        published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))

        if published_at >= published_after:
            videos.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail_url": snippet.get("thumbnails", {})
                    .get("medium", {})
                    .get("url", ""),
                    "published_at": published_at_str,
                }
            )

    return videos


def get_video_stats(video_id: str) -> dict | None:
    """Fetch current viewCount, likeCount, commentCount for a video."""
    youtube = _client()
    response = (
        youtube.videos()
        .list(part="statistics", id=video_id)
        .execute()
    )
    items = response.get("items", [])
    if not items:
        return None

    stats = items[0]["statistics"]
    return {
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
    }
