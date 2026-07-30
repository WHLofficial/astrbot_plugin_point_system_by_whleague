def contains_any(text: str, keywords: list) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False


def contains_all(text: str, keywords: list) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() not in text_lower:
            return False
    return True


def is_lottery_message(text: str, passphrase: str, lottery_keywords: list) -> bool:
    if not text or not passphrase:
        return False
    text_lower = text.lower()
    if passphrase.lower() not in text_lower:
        return False
    return contains_any(text, lottery_keywords)


def is_signin_message(text: str, sign_keywords: list) -> bool:
    return contains_any(text, sign_keywords)
