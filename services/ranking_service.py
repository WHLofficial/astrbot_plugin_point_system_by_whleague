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
