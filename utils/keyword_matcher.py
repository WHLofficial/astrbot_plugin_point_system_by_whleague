"""无前缀触发关键词匹配器（v0.2.1 起为严格匹配语义）。

触发判定：消息压缩全部空白后，必须与合法形态完全相等（大小写不敏感）。
带附加文本的消息（如"我要签到"、"whl 今天抽奖"）不再触发，
避免普通聊天消息被静默拦截（stop_event）。
"""

from ..config.defaults import parse_keyword_list

_RANKING_KEYWORDS = ("排行", "排名", "积分榜")

_MY_POINTS_KEYWORDS = ("我的积分", "积分查询")


def _normalize_keywords(keywords) -> list:
    if isinstance(keywords, str):
        return parse_keyword_list(keywords)
    if isinstance(keywords, (list, tuple)):
        return [str(k) for k in keywords if k]
    return []


def _norm(text: str) -> str:
    """压缩全部空白（含全角空格），用于触发形态比较。"""
    return "".join(text.split())


def _equals(text: str, keyword: str) -> bool:
    return _norm(text).lower() == _norm(keyword).lower()


def is_lottery_message(text: str, passphrase: str, lottery_keywords) -> bool:
    """抽奖触发判定：消息严格等于 关键词 / 口令+关键词 / 关键词+口令。"""
    if not text:
        return False
    norm_msg = _norm(text).lower()
    for kw in _normalize_keywords(lottery_keywords):
        kw_n = _norm(kw).lower()
        if not kw_n:
            continue
        if norm_msg == kw_n:
            return True
        if passphrase:
            p_n = _norm(passphrase).lower()
            if norm_msg == p_n + kw_n or norm_msg == kw_n + p_n:
                return True
    return False


def is_signin_message(text: str, sign_keywords) -> bool:
    """签到触发判定：消息严格等于某个签到关键词。"""
    if not text:
        return False
    return any(_equals(text, kw) for kw in _normalize_keywords(sign_keywords))


def is_ranking_message(text: str) -> bool:
    """排行触发判定：消息严格等于 排行/排名/积分榜 之一。"""
    if not text:
        return False
    return any(_equals(text, kw) for kw in _RANKING_KEYWORDS)


def is_my_points_message(text: str) -> bool:
    """我的积分触发判定：消息严格等于 我的积分/积分查询 之一。"""
    if not text:
        return False
    return any(_equals(text, kw) for kw in _MY_POINTS_KEYWORDS)
