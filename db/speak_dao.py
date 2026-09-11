"""speak_daily（发言统计明细）DAO。

一行为一个 (QQ, 群, 业务日)，msg_count 是该日累计条数。
stat_date 是业务日（受 signin_refresh_time 边界影响），跨日区间一律用
stat_date BETWEEN ? AND ? 过滤，不做时间戳换算——这样「本周/本月/上月」
与「今日」口径天然一致，将来加年度展示也只是换一组区间。
"""


class SpeakDAO:
    def __init__(self, db_manager):
        self._db = db_manager

    async def record(self, qq: str, group_id: str, stat_date: str) -> None:
        """记一条发言（幂等累加）。同一业务日重复调用只增加 msg_count。"""
        await self._db.execute(
            "INSERT INTO speak_daily (qq, group_id, stat_date, msg_count) "
            "VALUES (?,?,?,1) "
            "ON CONFLICT(qq, group_id, stat_date) DO UPDATE SET "
            "msg_count = msg_count + 1, "
            "updated_at = datetime('now','localtime')",
            (qq, group_id, stat_date),
        )

    async def get_total(self, qq: str, group_id: str) -> int:
        """该用户在本群的历史累计发言数（全量，不含周期过滤）。"""
        row = await self._db.fetchone(
            "SELECT COALESCE(SUM(msg_count),0) AS c FROM speak_daily "
            "WHERE qq=? AND group_id=?",
            (qq, group_id),
        )
        return int(row["c"]) if row else 0

    async def get_period_total(
        self, qq: str, group_id: str, start_date: str, end_date: str
    ) -> int:
        """该用户在本群某个业务日闭区间 [start_date, end_date] 内的发言数。"""
        row = await self._db.fetchone(
            "SELECT COALESCE(SUM(msg_count),0) AS c FROM speak_daily "
            "WHERE qq=? AND group_id=? AND stat_date BETWEEN ? AND ?",
            (qq, group_id, start_date, end_date),
        )
        return int(row["c"]) if row else 0

    async def get_group_ranking(self, group_id: str) -> list:
        """本群历史累计发言排行（含所有 qq）。

        Returns:
            Row 列表，每行含 qq、total 两列，按 total 降序、qq 升序。
            调用方据此自行推导名次、群总发言量、群参与人数。
        """
        return await self._db.fetchall(
            "SELECT qq, SUM(msg_count) AS total FROM speak_daily "
            "WHERE group_id=? GROUP BY qq ORDER BY total DESC, qq ASC",
            (group_id,),
        )

    async def get_daily_rows(self, qq: str, group_id: str) -> list:
        """该用户在本群的全部按日明细，stat_date 升序。

        Returns:
            Row 列表，每行含 stat_date、msg_count、updated_at 三列。
            供活跃轨迹（最近发言、活跃天数、最高单日、连续天数）在内存里推导。
        """
        return await self._db.fetchall(
            "SELECT stat_date, msg_count, updated_at FROM speak_daily "
            "WHERE qq=? AND group_id=? ORDER BY stat_date ASC",
            (qq, group_id),
        )
