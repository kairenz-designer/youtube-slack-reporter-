"""
ai_advisor.py — Claude-powered video performance recommendations.
Returns a short Vietnamese tip based on stats at each report time mark.
"""
import os
import sys
import anthropic

_client: anthropic.Anthropic | None = None

_SYSTEM = (
    "Bạn là chuyên gia phân tích hiệu suất video YouTube cho kênh nội dung Việt Nam. "
    "Khi nhận thống kê video, hãy đưa ra một nhận xét ngắn và lời khuyên hành động cụ thể bằng tiếng Việt. "
    "Trả lời trong 1–2 câu, không dùng emoji, không lặp lại số liệu đã có."
)

_HOUR_LABEL = {
    0.5: "30 phút", 1: "1 giờ", 2: "2 giờ", 3: "3 giờ",
    8: "8 giờ", 12: "12 giờ", 24: "24 giờ", 72: "3 ngày", 168: "1 tuần",
}


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
        f"{bench}\n\n"
        "Đưa ra nhận xét và lời khuyên hành động."
    )

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return text.strip() if text else None
    except Exception as exc:
        print(f"[AI] {exc}", file=sys.stderr)
        return None
