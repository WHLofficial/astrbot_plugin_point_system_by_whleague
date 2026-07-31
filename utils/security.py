import calendar
import re
from typing import Optional

_MAX_TEXT_LENGTH = 200


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    text = text.strip()[: _MAX_TEXT_LENGTH]
    return text


def parse_qq(raw: str) -> str:
    cleaned = raw.strip().lstrip("@")
    if not cleaned.isdigit():
        raise ValueError(f"Invalid QQ number: {raw}")
    return cleaned


def parse_qq_arg(raw: str) -> Optional[str]:
    """从 @用户 / @昵称(QQ) / 昵称(QQ) / [CQ:at,qq=...] 形式中提取 QQ 号。

    aiocqhttp 平台将 @ 段转为文本 ` @昵称(QQ号) `，纯数字参数按页码处理，
    因此本函数只识别「@」或「括号内含数字」的显式目标形式。

    Args:
        raw: 用户输入的参数文本。

    Returns:
        QQ 号字符串；无法识别为 @ 目标时返回 None。
    """
    s = raw.strip()
    if not s:
        return None
    m = re.search(r"\[CQ:at,qq=(\d+)\]", s)
    if m:
        return m.group(1)
    m = re.search(r"\((\d+)\)", s)
    if m:
        return m.group(1)
    if s.startswith("@"):
        m = re.match(r"^@(\d+)$", s)
        if m:
            return m.group(1)
    return None


def parse_int(raw: str, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    try:
        val = int(raw.strip())
    except (ValueError, TypeError):
        raise ValueError(f"Invalid integer: {raw}")
    if min_val is not None and val < min_val:
        raise ValueError(f"Value {val} is below minimum {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"Value {val} exceeds maximum {max_val}")
    return val


def parse_birthday(raw: str) -> str:
    s = raw.strip()
    m = re.match(r"^(\d{1,2})[月-](\d{1,2})[日]?$", s)
    if not m:
        raise ValueError(f"Invalid birthday format: {raw}, use MM-DD or MM月DD日")
    month = int(m.group(1))
    day = int(m.group(2))
    if not (1 <= month <= 12):
        raise ValueError(f"Invalid birthday month: {month}")
    max_day = calendar.monthrange(2000, month)[1]
    if not (1 <= day <= max_day):
        raise ValueError(f"Invalid birthday day: {day}")
    return f"{month:02d}-{day:02d}"
