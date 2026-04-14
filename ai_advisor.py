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

    above_avg = achievement_rate >= 100

    if above_avg:
        prompt = (
            f"Bạn là chuyên gia tối ưu nội dung YouTube.\n\n"
            f"Video đang đạt {achievement_rate}% so với trung bình kênh "
            f"tại mốc {report_hours}h sau khi đăng — hiệu suất TỐT.\n"
            f"- Tiêu đề: \"{title}\"\n"
            f"- Lượt xem: {stats['views']:,} | Trung bình kênh: {avg_views:,}\n"
            f"- Lượt thích: {stats['likes']:,} | Bình luận: {stats['comments']:,}\n\n"
            f"Nhìn vào thumbnail và tiêu đề, hãy phân tích CỤ THỂ điều gì đang hoạt động tốt:\n\n"
            f"• *Thumbnail:* yếu tố nào đang kéo click (bố cục, màu sắc, text thumb, biểu cảm, "
            f"độ tương phản...) — nêu chi tiết từng điểm mạnh\n"
            f"• *Tiêu đề:* yếu tố nào đang hoạt động tốt (từ khoá, tạo tò mò, độ dài, cấu trúc...) "
            f"— giải thích vì sao nó hiệu quả\n\n"
            f"Mục tiêu: giúp team hiểu công thức thành công để nhân rộng cho video sau.\n"
            f"Trả lời bằng tiếng Việt, ngắn gọn, thực chiến."
        )
    else:
        prompt = (
            f"Bạn là chuyên gia tối ưu nội dung YouTube.\n\n"
            f"Video đang đạt {achievement_rate}% so với trung bình kênh "
            f"tại mốc {report_hours}h sau khi đăng — cần cải thiện.\n"
            f"- Tiêu đề hiện tại: \"{title}\"\n"
            f"- Lượt xem: {stats['views']:,} | Trung bình kênh: {avg_views:,}\n"
            f"- Lượt thích: {stats['likes']:,} | Bình luận: {stats['comments']:,}\n\n"
            f"Nhìn vào thumbnail và tiêu đề, hãy đưa ra gợi ý SỬA CỤ THỂ — "
            f"không chỉ nói \"nên sửa\" mà phải đưa ra phương án thay thế thực tế:\n\n"
            f"• *Thumbnail:* chỉ ra vấn đề cụ thể (text quá dài, màu tối, thiếu mặt người...) "
            f"rồi gợi ý rõ ràng (ví dụ: rút text từ \"...\" thành \"...\", đổi màu nền sang...)\n"
            f"• *Tiêu đề:* chỉ ra vấn đề rồi đề xuất 1–2 phiên bản tiêu đề thay thế cụ thể\n\n"
            f"Trả lời bằng tiếng Việt, ngắn gọn, thực chiến."
        )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "url": thumbnail_url},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return message.content[0].text
    except Exception as e:
        print(f"[AI] Error getting recommendation: {e}")
        return None
