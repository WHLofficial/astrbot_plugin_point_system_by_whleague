import re

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


def parse_int(raw: str, min_val: int = None, max_val: int = None) -> int:
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
    month = m.group(1).zfill(2)
    day = m.group(2).zfill(2)
    return f"{month}-{day}"
