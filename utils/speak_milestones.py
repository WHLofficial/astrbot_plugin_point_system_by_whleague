"""发言里程碑播报：累计发言数跨过称号档位的那一刻 @ 本人播报。

档位与称号同源（都取配置 speak_stat_titles），只比称号多一层「跳过最低档」：
第一档阈值通常只有 1，人人第一条消息就命中，播报没有意义。

播报只取一个档位（一次跨多档只报最高的那个），并且逐档幂等——已登记过的最高档
决定下限，永不回退补报。静默基线也登记在同一张表里，所以任何时刻打开开关都不会
给老成员回溯播报。
"""

from .speak_titles import DEFAULT_TITLES, parse_title_rules

DEFAULT_TEMPLATE = (
    "🎉 恭喜达成 {total} 条发言里程碑，解锁称号「{title}」，本群第 {rank} 名！"
)
"""播报文案默认模板，与 _conf_schema.json 的 speak_milestone_template 默认值一致。"""


def parse_milestones(raw) -> list:
    """把配置解析成可播报档位 [(阈值, 称号)]，按阈值升序，跳过最低档。

    配置解析不出合法项时整表回退内置默认档位，不抛异常。
    """
    rules = parse_title_rules(raw) or list(DEFAULT_TITLES)
    ordered = sorted(rules, key=lambda pair: pair[0])
    # 只配了一档时保留它：否则「跳过最低档」会让功能配了也永不触发
    return ordered[1:] if len(ordered) > 1 else ordered


def pick_milestone(tiers, fired_max: int, total: int) -> int | None:
    """返回 (fired_max, total] 区间内最高的档位阈值，没有则 None。

    fired_max 是已登记过的最高档，0 表示尚无任何登记（含惰性基线后的状态）。
    """
    hit = [threshold for threshold, _ in tiers if fired_max < threshold <= total]
    return max(hit) if hit else None


class _KeepPlaceholder(dict):
    """未知占位符原样保留，免得写错一个花括号就整条播报发不出去。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def render_milestone(template, *, total: int, title: str, rank: int) -> str:
    """渲染播报文案；文案为空或花括号写法非法时回退内置默认模板。"""
    values = _KeepPlaceholder(total=total, title=title, rank=rank)
    for candidate in (str(template or "").strip(), DEFAULT_TEMPLATE):
        if not candidate:
            continue
        try:
            return candidate.format_map(values)
        except (ValueError, IndexError, TypeError, KeyError):
            continue
    # 兜底：默认模板本身也被写坏时不再依赖 str.format
    return f"🎉 恭喜达成 {total} 条发言里程碑，称号「{title}」"
