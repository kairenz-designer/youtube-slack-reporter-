import os
import sys
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from ai_advisor import get_ai_tip

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL   = os.environ["SLACK_CHANNEL"]

_client = WebClient(token=SLACK_BOT_TOKEN)

HOUR_LABELS = {1: "1 giờ", 3: "3 giờ", 6: "6 giờ", 24: "24 giờ"}

THRESHOLD_GREEN  = 100
THRESHOLD_YELLOW = 70


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
        return "\U0001f7e2"
    if rate >= THRESHOLD_YELLOW:
        return "\U0001f7e1"
    return "\U0001f534"


def _thumbnail_url(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"


def send_report(
    video: dict,
    stats: dict,
    report_hours: float,
    previous_stats: dict | None = None,
    benchmark: list[dict] | None = None,
    predicted_24h: tuple | None = None,
    comment_summary: str | None = None,
):
    label = HOUR_LABELS.get(report_hours, f"{report_hours} giờ")
    prev  = previous_stats or {}

    views_str    = f"{stats['views']:,}{_diff(stats['views'],    prev.get('views'))}"
    likes_str    = f"{stats['likes']:,}{_diff(stats['likes'],    prev.get('likes'))}"
    comments_str = f"{stats['comments']:,}{_diff(stats['comments'], prev.get('comments'))}"

    avg_views        = None
    achievement_rate = None
    if benchmark and len(benchmark) >= 2:
        avg_views        = int(sum(b["views"] for b in benchmark) / len(benchmark))
        achievement_rate = round(stats["views"] / avg_views * 100) if avg_views > 0 else None

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Upload {label} – Hiệu suất video",
                "emoji": True,
            },
        },
        {
            "type": "image",
            "image_url": _thumbnail_url(video["video_id"]),
            "alt_text": video["title"],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*<{video['url']}|{video['title']}>*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Lượt xem*\n{views_str}"},
                {"type": "mrkdwn", "text": f"*Lượt thích*\n{likes_str}"},
                {"type": "mrkdwn", "text": f"*Bình luận*\n{comments_str}"},
            ],
        },
    ]

    # Benchmark
    if avg_views is not None and achievement_rate is not None:
        indicator = _achievement_indicator(achievement_rate)
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Trung bình {len(benchmark)} video gần nhất ({label})*\n{avg_views:,} lượt xem",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Đạt được*\n{achievement_rate}% {indicator}",
                },
            ],
        })
    elif benchmark is not None and len(benchmark) < 2:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"ℹ️ Chưa đủ dữ liệu để so sánh ({len(benchmark)}/2 video)."}],
        })

    # 24h prediction (chỉ hiện ở report trước 24h)
    if predicted_24h is not None:
        pred_views, sample_count = predicted_24h
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🔮 *Dự báo 24h:* ~{pred_views:,} lượt xem _(dựa trên {sample_count} video lịch sử)_",
            },
        })

    # Comment summary (chỉ hiện ở report 24h)
    if comment_summary:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"💬 *Phản hồi khán giả:*\n{comment_summary}",
            },
        })

    # AI tip
    ai_tip = get_ai_tip(
        title=video["title"],
        report_hours=report_hours,
        views=stats["views"],
        likes=stats["likes"],
        comments=stats["comments"],
        achievement_rate=achievement_rate,
        avg_views=avg_views,
        prev_views=prev.get("views") if prev else None,
    )
    if ai_tip:
        tip_text = f"💡 {ai_tip}"
    elif achievement_rate is not None:
        if achievement_rate >= THRESHOLD_GREEN:
            tip_text = "💡 Video đang hiệu suất tốt! Tiếp tục duy trì thumbnail và tiêu đề theo hướng này."
        elif achievement_rate >= THRESHOLD_YELLOW:
            tip_text = "💡 Hiệu suất ở mức trung bình. Theo dõi thêm ở mốc tiếp theo."
        else:
            tip_text = "💡 Lượt xem đang thấp hơn trung bình kênh. Hãy xem xét thay đổi thumbnail hoặc tiêu đề."
    else:
        tip_text = None
    if tip_text:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": tip_text}],
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"🔗 <{video['url']}|Xem trên YouTube>"}],
    })

    try:
        _client.chat_postMessage(
            channel=SLACK_CHANNEL,
            blocks=blocks,
            text=f"Báo cáo video sau {report_hours}h: {video['title']}",
            unfurl_links=False,
            unfurl_media=False,
        )
        print(f"[Slack] Sent {report_hours}h report: {video['video_id']}", file=sys.stderr)
    except SlackApiError as e:
        print(f"[Slack] Error: {e.response['error']}", file=sys.stderr)
        raise


def send_revival_alert(video: dict, current_views: int, velocity_3h: int, baseline_3h: int):
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔥 Video đang hồi sinh!", "emoji": True},
        },
        {
            "type": "image",
            "image_url": _thumbnail_url(video["video_id"]),
            "alt_text": video["title"],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*<{video['url']}|{video['title']}>*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Tăng trong 3h qua*\n+{velocity_3h:,} lượt xem"},
                {"type": "mrkdwn", "text": f"*Baseline bình thường (3h)*\n~{baseline_3h:,} lượt xem"},
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Tổng hiện tại: {current_views:,} · 🔗 <{video['url']}|Xem trên YouTube>"}],
        },
    ]
    try:
        _client.chat_postMessage(
            channel=SLACK_CHANNEL,
            blocks=blocks,
            text=f"🔥 Video hồi sinh: {video['title']}",
            unfurl_links=False,
            unfurl_media=False,
        )
        print(f"[Slack] Revival alert: {video['video_id']}", file=sys.stderr)
    except SlackApiError as e:
        print(f"[Slack] Revival alert error: {e.response['error']}", file=sys.stderr)


def send_weekly_analysis(analysis_text: str, video_count: int):
    from datetime import datetime
    week = datetime.utcnow().strftime("tuần %d/%m/%Y")
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 Phân tích title — {week}", "emoji": True},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": analysis_text},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_Phân tích từ {video_count} video gần nhất có đủ dữ liệu 24h_"}],
        },
    ]
    try:
        _client.chat_postMessage(
            channel=SLACK_CHANNEL,
            blocks=blocks,
            text=f"Phân tích title hàng tuần ({video_count} video)",
            unfurl_links=False,
            unfurl_media=False,
        )
        print("[Slack] Sent weekly analysis", file=sys.stderr)
    except SlackApiError as e:
        print(f"[Slack] Weekly analysis error: {e.response['error']}", file=sys.stderr)
