# 本群榜拉取候选时在 Top N 之外多取的余量：过滤掉已退群账号后仍有名次补位。
_FILTER_MARGIN = 20


class RankingService:
    def __init__(self, dao):
        self._dao = dao

    async def get_ranking(
        self, group_id: str, top_n: int = 10, member_qqs: set | None = None
    ) -> dict:
        """本群积分榜（本群不足 3 人时回退全局榜）。

        member_qqs 非 None 时（当前群成员集合），本群榜剔除不在集合里的账号
        （已退群/被踢等，users 关联行会长期残留），候选按 top_n+余量 放大，
        过滤后再截取 top_n；回退全局与否在过滤后判断。全局榜跨群口径不过滤。
        member_qqs 为 None（成员接口不可用等）时不过滤，榜单可用性优先。
        """
        fetch_n = top_n + _FILTER_MARGIN if member_qqs is not None else top_n
        group_users = await self._dao.get_top_n_by_group(
            group_id, fetch_n, min_points=1
        )
        if member_qqs is not None:
            group_users = [
                u for u in group_users if str(u["qq"]) in member_qqs
            ]
        group_users = group_users[:top_n]
        is_global = False
        if len(group_users) < 3:
            global_users = await self._dao.get_top_n_global(top_n, min_points=1)
            if len(global_users) > len(group_users):
                group_users = global_users
                is_global = True

        return {"users": group_users, "is_global": is_global}

    async def get_self_rank(
        self,
        qq: str,
        group_id: str,
        is_global: bool,
        member_qqs: set | None = None,
    ) -> tuple | None:
        """查询触发者自己的排名（与当前榜单口径一致）。

        Args:
            qq: 触发者 QQ。
            group_id: 当前群（本群榜用）。
            is_global: 当前榜单是否为全局榜。
            member_qqs: 当前群成员集合；仅本群口径生效，全局榜忽略
                （跨群账号无成员概念）。

        Returns:
            (rank, points, group_id)：rank 1 起、同分同名次；group_id 全局榜为
            最近活跃群、本群榜为当前群。未上榜（积分低于 1 或无账户/非本群成员）
            返回 None。
        """
        if is_global:
            return await self._dao.get_rank_global(qq, min_points=1)
        rank = await self._dao.get_rank_in_group(
            qq, group_id, min_points=1, member_qqs=member_qqs
        )
        if rank is None:
            return None
        points = await self._dao.get_balance(qq)
        return rank, points, group_id
