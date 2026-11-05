"""群成员信息获取（平台 API 调用的唯一入口）。"""


async def fetch_member_info(bot, qq: str, group_id: str) -> dict | None:
    """调用平台 get_group_member_info 获取成员信息。

    任何失败（无 bot、异常、非 dict 返回）都静默返回 None，
    由调用方决定回退策略。

    Args:
        bot: 平台 bot 实例（须有 call_action 方法）；None 时直接返回 None。
        qq: 成员 QQ。
        group_id: 群 ID。

    Returns:
        成员信息 dict（含 card/nickname 等字段），失败返回 None。
    """
    call = getattr(bot, "call_action", None)
    if not call:
        return None
    try:
        info = await call(
            action="get_group_member_info",
            group_id=int(group_id),
            user_id=int(qq),
            no_cache=True,
        )
        if isinstance(info, dict):
            return info
    except Exception:
        return None
    return None


async def fetch_member_qqs(bot, group_id: str) -> set[str] | None:
    """调用平台 get_group_member_list 获取当前群成员 QQ 集合。

    任何失败（无 bot、异常、非列表/空返回）都静默返回 None，
    由调用方决定回退策略（None 表示无法判定成员身份，应保持不过滤）。
    不带 no_cache：走协议端缓存，比逐人 get_group_member_info 更稳。

    Args:
        bot: 平台 bot 实例（须有 call_action 方法）；None 时直接返回 None。
        group_id: 群 ID。

    Returns:
        成员 QQ 字符串集合；失败或拿不到有效列表返回 None。
    """
    call = getattr(bot, "call_action", None)
    if not call:
        return None
    try:
        members = await call(
            action="get_group_member_list", group_id=int(group_id)
        )
    except Exception:
        return None
    if not isinstance(members, list):
        return None
    qqs = set()
    for m in members:
        if isinstance(m, dict) and m.get("user_id") is not None:
            qqs.add(str(m["user_id"]))
    return qqs or None
