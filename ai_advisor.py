"""
AI-powered video performance advisor using Claude claude-opus-4-6.
Analyzes thumbnail + title when achievement rate < 100%.
"""
import os
import anthropic


def get_ai_recommendation(
    title: str,
    thumbnail_url: str,
    stats: dict,
    report_hours: int,
    achievement_rate: float,
    avg_views: int,
) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=350,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "url": thumbnail_url},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Bạn là chuyên gia tối ưu nội dung YouTube.\n\n"
                            f"Video đang đạt {achievement_rate}% so với trung bình kênh "
                            f"tại mốc {report_hours}h sau khi đăng.\n"
                            f"- Tiêu đề: \"{title}\"\n"
                            f"- Lượt xem: {stats['views']:,} | Trung bình kênh: {avg_views:,}\n"
                            f"- Lượt thích: {stats['likes']:,} | Bình luận: {stats['comments']:,}\n\n"
                            f"Nhìn vào thumbnail trên và tiêu đề, hãy đánh giá ngắn gọn và "
                            f"đưa ra khuyến nghị cụ thể (dưới 80 từ):\n"
                            f"• *Thumbnail:* điểm chưa tốt + gợi ý cải thiện\n"
                            f"• *Tiêu đề:* điểm chưa tốt + gợi ý cải thiện"
                        ),
                    },
                ],
            }],
        )
        return message.content[0].text
    except Exception as e:
        print(f"[AI] Error getting recommendation: {e}")
        return None
