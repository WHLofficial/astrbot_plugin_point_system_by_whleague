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
