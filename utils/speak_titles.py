"""发言称号：按「本群历史累计发言数」映射到一个主称号。

只取一个称号（阈值降序找到第一个命中的），不做多标签叠加。
阈值是闭区间下界：累计数恰好等于阈值即命中该档。
"""

from ..config.defaults import parse_keyword_list

# 内置默认档位 [(阈值, 称号)]，与 _conf_schema.json 的 speak_stat_titles 默认值一致。
DEFAULT_TITLES = [
    (1, "🌱 群内新面孔"),
    (20, "💬 常驻群友"),
    (100, "🗣 话题担当"),
    (400, "🔥 聊天主力"),
    (1200, "🌊 水群之王"),
    (4000, "👑 群聊之魂"),
    (12000, "🏆 传说话痨"),
]

_FALLBACK_TITLE = DEFAULT_TITLES[0][1]
"""累计数低于配置里所有阈值时使用的兜底称号。"""


def parse_title_rules(raw) -> list:
    """把配置解析成 [(阈值, 称号)]，按阈值降序。

    支持 list 型配置项或逗号分隔文本，每项形如 "400:🔥 聊天主力"。
    跳过阈值非正整数的项（0、-5、abc 都丢弃）与称号为空的项；
    同阈值重复时保留先出现的那个。解析不出任何合法项时返回空列表。
    """
    rules = []
    seen = set()
    for item in parse_keyword_list(raw):
        text = str(item).strip()
        head, sep, title = text.partition(":")
        if not sep:
            continue
        title = title.strip()
        if not title:
            continue
        try:
            threshold = int(head.strip())
        except ValueError:
            continue
        if threshold < 1 or threshold in seen:
            continue
        seen.add(threshold)
        rules.append((threshold, title))
    rules.sort(key=lambda pair: pair[0], reverse=True)
    return rules


def resolve_title(total: int, raw_rules) -> str:
    """返回累计发言数命中的主称号。

    配置解析不出合法项时整表回退内置默认档位，不抛异常。
    """
    rules = parse_title_rules(raw_rules) or DEFAULT_TITLES
    # DEFAULT_TITLES 是按阈值升序写的，这里统一降序后再取首个命中，避免低档先命中
    for threshold, title in sorted(rules, key=lambda item: item[0], reverse=True):
        if total >= threshold:
            return title
    return _FALLBACK_TITLE
