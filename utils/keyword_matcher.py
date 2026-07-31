from ..config.defaults import parse_keyword_list


def _normalize_keywords(keywords) -> list:
    if isinstance(keywords, str):
        return parse_keyword_list(keywords)
    if isinstance(keywords, (list, tuple)):
        return [str(k) for k in keywords if k]
    return []


def contains_any(text: str, keywords) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for kw in _normalize_keywords(keywords):
        if kw.lower() in text_lower:
            return True
    return False


def is_lottery_message(text: str, passphrase: str, lottery_keywords) -> bool:
    if not text or not passphrase:
        return False
    text_lower = text.lower()
    if passphrase.lower() not in text_lower:
        return False
    return contains_any(text, lottery_keywords)


def is_signin_message(text: str, sign_keywords) -> bool:
    return contains_any(text, sign_keywords)
