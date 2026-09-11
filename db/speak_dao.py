"""speak_daily（发言统计明细）DAO。

一行为一个 (QQ, 群, 业务日)，msg_count 是该日累计条数。
stat_date 是业务日（受 signin_refresh_time 边界影响），跨日区间一律用
stat_date BETWEEN ? AND ? 过滤，不做时间戳换算——这样「本周/本月/上月」
与「今日」口径天然一致，将来加年度展示也只是换一组区间。
"""

from ..utils.speak_milestones import pick_milestone

_SQL_RECORD = (
    "INSERT INTO speak_daily (qq, group_id, stat_date, msg_count) "
    "VALUES (?,?,?,1) "
    "ON CONFLICT(qq, group_id, stat_date) DO UPDATE SET "
    "msg_count = msg_count + 1, "
    "updated_at = datetime('now','localtime')"
)

_SQL_TOTAL = (
    "SELECT COALESCE(SUM(msg_count),0) AS c FROM speak_daily WHERE qq=? AND group_id=?"
)

_SQL_MARK_MILESTONE = (
    "INSERT OR IGNORE INTO speak_milestone_log (qq, group_id, milestone) VALUES (?,?,?)"
)

_SQL_MILESTONE_STATE = (
    "SELECT COUNT(*) AS c, COALESCE(MAX(milestone),0) AS m "
    "FROM speak_milestone_log WHERE qq=? AND group_id=?"
)


class SpeakDAO:
    def __init__(self, db_manager):
        self._db = db_manager

    async def record(self, qq: str, group_id: str, stat_date: str) -> None:
        """记一条发言（幂等累加）。同一业务日重复调用只增加 msg_count。"""
        await self.record_and_claim(qq, group_id, stat_date)

    async def record_and_claim(
        self, qq: str, group_id: str, stat_date: str, milestones=None
    ) -> tuple[int, int | None]:
        """记一条发言，并在同一次事务里判定这条消息是否跨过某个可播报档位。

        Args:
            milestones: 可播报档位 [(阈值, 称号)]，传 None/空表示只计数不播报。

        Returns:
            (本群累计发言数, 本次刚刚跨过并已登记的档位阈值)；无播报时第二项 None。

        计数、求和、读登记状态、惰性静默基线、抢占档位必须在同一事务里完成：分成
        多次加锁时，并发发言下后到的那次会读到旧状态，把「恰好被这条消息跨过的
        档位」当成早该静默的基线登记掉，那一档就再也不播报（漏报且不可恢复）。
        同事务后，登记到累计数 T 的那次必然就是把它从 T-1 推到 T 的消息，故不漏报。

        登记先于播报（本次返回值非 None 即已落库），并发下同一档只有一路拿到非
        None，另一路因 INSERT OR IGNORE 影响 0 行而为 None，故不会重复播报。
        """
        tier_list = list(milestones or [])

        async def _tx(conn):
            await conn.execute(_SQL_RECORD, (qq, group_id, stat_date))
            cur = await conn.execute(_SQL_TOTAL, (qq, group_id))
            row = await cur.fetchone()
            await cur.close()
            total = int(row["c"]) if row else 0
            if not tier_list:
                return total, None

            cur = await conn.execute(_SQL_MILESTONE_STATE, (qq, group_id))
            state = await cur.fetchone()
            await cur.close()
            registered = int(state["c"]) if state else 0
            fired_max = int(state["m"]) if state else 0
            if registered == 0:
                # 首次检查：这条消息之前已跨过的档位静默登记，避免给老成员回溯播报
                baseline = [t for t, _ in tier_list if t <= total - 1]
                for threshold in baseline:
                    await conn.execute(
                        _SQL_MARK_MILESTONE, (qq, group_id, int(threshold))
                    )
                fired_max = max(baseline) if baseline else 0

            target = pick_milestone(tier_list, fired_max, total)
            if target is None:
                return total, None
            cur = await conn.execute(_SQL_MARK_MILESTONE, (qq, group_id, int(target)))
            claimed = cur.rowcount > 0
            await cur.close()
            return total, int(target) if claimed else None

        return await self._db.execute_transaction(_tx)

    async def get_total(self, qq: str, group_id: str) -> int:
        """该用户在本群的历史累计发言数（全量，不含周期过滤）。"""
        row = await self._db.fetchone(_SQL_TOTAL, (qq, group_id))
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
