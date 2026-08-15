class RankingService:
    def __init__(self, dao):
        self._dao = dao

    async def get_ranking(self, group_id: str, top_n: int = 10) -> dict:
        group_users = await self._dao.get_top_n_by_group(group_id, top_n, min_points=1)
        is_global = False
        if len(group_users) < 3:
            global_users = await self._dao.get_top_n_global(top_n, min_points=1)
            if len(global_users) > len(group_users):
                group_users = global_users
                is_global = True

        return {"users": group_users, "is_global": is_global}

    async def get_self_rank(
        self, qq: str, group_id: str, is_global: bool
    ) -> tuple | None:
        """查询触发者自己的排名（与当前榜单口径一致）。

        Args:
            qq: 触发者 QQ。
            group_id: 当前群（本群榜用）。
            is_global: 当前榜单是否为全局榜。

        Returns:
            (rank, points, group_id)：rank 1 起、同分同名次；group_id 全局榜为
            最近活跃群、本群榜为当前群。未上榜（积分低于 1 或无账户/非本群成员）
            返回 None。
        """
        if is_global:
            return await self._dao.get_rank_global(qq, min_points=1)
        rank = await self._dao.get_rank_in_group(qq, group_id, min_points=1)
        if rank is None:
            return None
        points = await self._dao.get_balance(qq)
        return rank, points, group_id
