import os
import sys
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from ai_advisor import get_ai_recommendation

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL   = os.environ["SLACK_CHANNEL"]

_client = WebClient(token=SLACK_BOT_TOKEN)

HOUR_LABELS = {1: "1 gi\u1edd", 3: "3 gi\u1edd", 6: "6 gi\u1edd", 24: "24 gi\u1edd"}

# Achievement rate thresholds
THRESHOLD_GREEN  = 100   # >= 100% → 🟢
THRESHOLD_YELLOW = 70    # >= 70%  → 🟡
                         # <  70%  → 🔴


def _diff(current: int, previous: int | None) -> str:
    if previous is None:
        return ""
    delta = current - previous
    if delta > 0:
        return f" _(+{delta:,})_"
    if delta < 0:
        return f" _({delta:,})_"
    return " _(+0)_"


def _achievement_indicator(rate: float) -> str:
    if rate >= THRESHOLD_GREEN:
        return "\U0001f7e2"   # 🟢
    if rate >= THRESHOLD_YELLOW:
        return "\U0001f7e1"   # 🟡
    return "\U0001f534"       # 🔴


def _thumbnail_url(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"


def send_report(
    video: dict,
    stats: dict,
    report_hours: int,
    previous_stats: dict | None = None,
    ctr: float | None = None,
    previous_ctr: float | None = None,
    benchmark: list[dict] | None = None,
):
    label = HOUR_LABELS.get(report_hours, f"{report_hours} gi\u1edd")
    prev  = previous_stats or {}

    views_str    = f"{stats['views']:,}{_diff(stats['views'],    prev.get('views'))}"
    likes_str    = f"{stats['likes']:,}{_diff(stats['likes'],    prev.get('likes'))}"
    comments_str = f"{stats['comments']:,}{_diff(stats['comments'], prev.get('comments'))}"

    # --- Benchmark calculation ---
    avg_views        = None
    achievement_rate = None
    if benchmark and len(benchmark) >= 3:
        avg_views        = int(sum(b["views"] for b in benchmark) / len(benchmark))
        achievement_rate = round(stats["views"] / avg_views * 100) if avg_views > 0 else None

    # --- Build blocks ---
    blocks = [
        # Header
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"\U0001f4ca Upload {label} \u2013 Hi\u1ec7u su\u1ea5t video",
                "emoji": True,
            },
        },
        # Thumbnail image
        {
            "type": "image",
            "image_url": _thumbnail_url(video["video_id"]),
            "alt_text": video["title"],
        },
        # Video title link
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{video['url']}|{video['title']}>*",
            },
        },
        # Core stats
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*L\u01b0\u1ee3t xem*\n{views_str}"},
                {"type": "mrkdwn", "text": f"*L\u01b0\u1ee3t th\xedch*\n{likes_str}"},
                {"type": "mrkdwn", "text": f"*B\xecnh lu\u1eadn*\n{comments_str}"},
            ],
        },
    ]

    # CTR field (append to stats if available)
    if ctr is not None:
        ctr_diff = ""
        if prev.get("ctr") is not None:
            delta = round(ctr - prev["ctr"], 2)
            ctr_diff = f" _(+{delta}%)_" if delta > 0 else (f" _({delta}%)_" if delta < 0 else " _(+0%)_")
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*CTR*\n{ctr}%{ctr_diff}"},
            ],
        })

    # Benchmark block
    if avg_views is not None and achievement_rate is not None:
        indicator = _achievement_indicator(achievement_rate)
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"*Trung b\xecnh {len(benchmark)} video g\u1ea7n nh\u1ea5t ({label})*\n"
                        f"{avg_views:,} l\u01b0\u1ee3t xem"
                    ),
                },
                {
                    "type": "mrkdwn",
                    "text": f"*\u0110\u1ea1t \u0111\u01b0\u1ee3c*\n{achievement_rate}% {indicator}",
                },
            ],
        })

        # AI recommendation when below 100%
        if achievement_rate < THRESHOLD_GREEN:
            ai_text = get_ai_recommendation(
                title=video["title"],
                thumbnail_url=_thumbnail_url(video["video_id"]),
                stats=stats,
                report_hours=report_hours,
                achievement_rate=achievement_rate,
                avg_views=avg_views,
            )
            if ai_text:
                blocks.append({"type": "divider"})
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"\U0001f916 *\u0110\xe1nh gi\xe1 & Kh\u01b0y\u1ebfn ngh\u1ecb (AI)*\n{ai_text}",
                    },
                })

    elif benchmark is not None and len(benchmark) < 3:
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "\u2139\ufe0f Ch\u01b0a \u0111\u1ee7 d\u1eef li\u1ec7u \u0111\u1ec3 so s\xe1nh. C\u1ea7n \xedt nh\u1ea5t 3 video tr\u01b0\u1edbc \u0111\xf3.",
            }],
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"\U0001f517 <{video['url']}|Xem tr\xean YouTube>"}
        ],
    })

    try:
        _client.chat_postMessage(
            channel=SLACK_CHANNEL,
            blocks=blocks,
            text=f"Bao cao video sau {report_hours}h: {video['title']}",
            unfurl_links=False,
            unfurl_media=False,
        )
        print(f"[Slack] Sent {report_hours}h report: {video['video_id']}", file=sys.stderr)
    except SlackApiError as e:
        print(f"[Slack] Error: {e.response['error']}", file=sys.stderr)
        raise
