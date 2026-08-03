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


def parse_rob_message(components, rob_keywords, self_qq) -> dict:
    """打劫形态消息解析（v0.4.0）：At 段与关键词分离，组件属性 duck-typing 判定。

    打劫形态 = 消息含有效 @ 目标（排除 AtAll 与 bot 自身），
    且其余 Plain 文本压缩空白后严格等于某打劫关键词（顺序无关）。

    Args:
        components: event.get_messages() 返回的消息组件列表。
        rob_keywords: 打劫关键词列表。
        self_qq: bot 自身 QQ（排除 @bot）。

    Returns:
        {
            "targets": list[str],      # 有效 @ 目标，保序
            "has_invalid_at": bool,    # 是否存在 @all / @bot 等无效 @
            "text_match": bool,        # 其余 Plain 文本是否严格等于某打劫关键词
        }
    """
    keywords = _normalize_keywords(rob_keywords)
    targets = []
    invalid_at = False
    texts = []
    self_id = str(self_qq) if self_qq is not None else ""
    for c in components or []:
        ctype = str(getattr(c, "type", "")).lower()
        if ctype == "at":
            qq_raw = getattr(c, "qq", None)
            q = str(qq_raw) if qq_raw is not None else ""
            if not q or q.lower() == "all" or q == self_id:
                invalid_at = True
            else:
                targets.append(q)
        elif ctype == "plain":
            texts.append(getattr(c, "text", ""))
    norm_text = _norm("".join(texts)).lower()
    text_match = any(norm_text == _norm(k).lower() for k in keywords)
    return {
        "targets": targets,
        "has_invalid_at": invalid_at,
        "text_match": text_match,
    }


def is_rob_message(components, rob_keywords, self_qq) -> bool:
    """打劫触发判定：含有效 @ 目标且其余 Plain 文本严格等于某打劫关键词。"""
    parsed = parse_rob_message(components, rob_keywords, self_qq)
    return bool(parsed["targets"]) and parsed["text_match"]
