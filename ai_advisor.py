"""
ai_advisor.py — Claude-powered insights for YouTube video performance.
"""
import os
import sys
import anthropic

_client: anthropic.Anthropic | None = None

_SYSTEM_TIP = (
    "Bạn là chuyên gia phân tích hiệu suất video YouTube cho kênh nội dung Việt Nam. "
    "Khi nhận thống kê video, hãy đưa ra một nhận xét ngắn và lời khuyên hành động cụ thể bằng tiếng Việt. "
    "Trả lời trong 1–2 câu, không dùng emoji, không lặp lại số liệu đã có."
)

_HOUR_LABEL = {1: "1 giờ", 3: "3 giờ", 6: "6 giờ", 24: "24 giờ"}


def _client_or_none() -> anthropic.Anthropic | None:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        _client = anthropic.Anthropic(api_key=key)
    return _client


def get_ai_tip(
    title: str,
    report_hours: float,
    views: int,
    likes: int,
    comments: int,
    achievement_rate: float | None,
    avg_views: int | None,
    prev_views: int | None,
) -> str | None:
    client = _client_or_none()
    if client is None:
        return None

    label = _HOUR_LABEL.get(report_hours, f"{report_hours}h")
    delta = ""
    if prev_views is not None:
        d = views - prev_views
        delta = f" (+{d:,})" if d >= 0 else f" ({d:,})"
    bench = ""
    if achievement_rate is not None and avg_views is not None:
        bench = f"\nĐạt {achievement_rate}% so với trung bình kênh ({avg_views:,} lượt xem)"

    prompt = (
        f'Video: "{title}"\n'
        f"Mốc: sau {label}\n"
        f"Lượt xem: {views:,}{delta} | Thích: {likes:,} | Bình luận: {comments:,}"
        f"{bench}\n\nĐưa ra nhận xét và lời khuyên hành động."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            system=_SYSTEM_TIP,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return text.strip() if text else None
    except Exception as exc:
        print(f"[AI] tip error: {exc}", file=sys.stderr)
        return None


def get_comment_summary(title: str, comments: list[dict]) -> str | None:
    """Claude tóm tắt phản hồi của khán giả từ comment section."""
    client = _client_or_none()
    if client is None or not comments:
        return None

    lines = "\n".join(f"- {c['author']}: {c['text'][:200]}" for c in comments[:50])
    prompt = (
        f'Video: "{title}"\n\n'
        f"Bình luận từ khán giả:\n{lines}\n\n"
        "Tóm tắt ngắn gọn: khán giả đang nói gì, hỏi gì, và cảm nhận chung thế nào?"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            system=(
                "Bạn tóm tắt bình luận YouTube bằng tiếng Việt trong 2–3 câu súc tích. "
                "Không dùng emoji, không lặp lại tên video."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return text.strip() if text else None
    except Exception as exc:
        print(f"[AI] comment summary error: {exc}", file=sys.stderr)
        return None


def get_title_analysis(videos: list[dict]) -> str | None:
    """Claude phân tích pattern title và đưa ra insight cho kênh."""
    client = _client_or_none()
    if client is None or len(videos) < 5:
        return None

    lines = "\n".join(
        f"- \"{v['title']}\" → {v['views']:,} views (24h), {v['likes']:,} likes"
        for v in sorted(videos, key=lambda x: x["views"], reverse=True)
    )
    prompt = (
        f"Đây là các video YouTube của kênh và hiệu suất sau 24h:\n\n{lines}\n\n"
        "Hãy phân tích: title dạng nào đang hoạt động tốt nhất? "
        "Có pattern nào nổi bật (câu hỏi, có số, tên người, độ dài, cấu trúc)? "
        "Đưa ra 2–3 insight cụ thể và 1 gợi ý cho video tiếp theo."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            system=(
                "Bạn là chuyên gia phân tích content YouTube cho kênh tiếng Việt. "
                "Phân tích thực tế, súc tích, bằng tiếng Việt."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return text.strip() if text else None
    except Exception as exc:
        print(f"[AI] title analysis error: {exc}", file=sys.stderr)
        return None
