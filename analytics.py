"""
YouTube Analytics API client.
Fetches impressionClickThroughRate for a video.
Requires OAuth 2.0 — token is saved to token.json after the first browser auth.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]
CLIENT_SECRET_FILE = Path(__file__).parent / "client_secret.json"
TOKEN_FILE = Path(__file__).parent / "token.json"

YOUTUBE_CHANNEL_ID = os.environ["YOUTUBE_CHANNEL_ID"]
YOUTUBE_OWNER_EMAIL = os.environ.get("YOUTUBE_OWNER_EMAIL", "")


def _get_credentials() -> Credentials:
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_FILE), SCOPES
            )
            # login_hint pre-selects the correct Google account in the browser
            kwargs = {"login_hint": YOUTUBE_OWNER_EMAIL} if YOUTUBE_OWNER_EMAIL else {}
            creds = flow.run_local_server(port=0, **kwargs)

        TOKEN_FILE.write_text(creds.to_json())

    return creds


def _analytics_client():
    return build("youtubeAnalytics", "v2", credentials=_get_credentials())


def get_views_first_day(video_id: str, published_at: str) -> int | None:
    """
    Return total views on the publish day (day-level is max granularity Analytics API supports).
    Used as approximate 24h benchmark seed.
    """
    try:
        youtube = _analytics_client()
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00")).replace(tzinfo=None)
        start_date = pub.strftime("%Y-%m-%d")
        end_date = (pub + timedelta(days=1)).strftime("%Y-%m-%d")

        response = (
            youtube.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views",
                dimensions="day",
                filters=f"video=={video_id}",
            )
            .execute()
        )

        rows = response.get("rows", [])
        if not rows:
            return None

        total = sum(int(row[1]) for row in rows)
        return total if total > 0 else None

    except Exception as e:
        print(f"[Analytics] Could not fetch day-1 views for {video_id}: {e}", file=sys.stderr)
        return None


def get_video_ctr(video_id: str, published_at: str) -> dict | None:
    """
    CTR (impressionClickThroughRate) is NOT available in the YouTube Analytics
    API v2 for channel owners — it only exists in YouTube Studio's private API.
    This function always returns None.
    """
    return None
