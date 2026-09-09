"""sync_ledger（竞猜同步幂等账本）DAO。

只存流水凭证（payout_id → 入账记录），不存余额；
余额唯一真源仍是 accounts.points。
credited_at 为 UTC；对账汇总按东八区日期过滤。
"""


class SyncDAO:
    def __init__(self, db_manager):
        self._db = db_manager

    async def get_by_payout_id(self, payout_id: str):
        return await self._db.fetchone(
            "SELECT * FROM sync_ledger WHERE payout_id=?", (payout_id,)
        )

    async def insert_credited(
        self,
        conn,
        payout_id: str,
        qq_id: str,
        amount: int,
        type_: str,
        event_id: int | None,
    ):
        """事务回调内调用：写幂等账本行。

        payout_id 冲突时抛 sqlite3.IntegrityError（幂等判定的依据）。
        """
        await conn.execute(
            "INSERT INTO sync_ledger (payout_id, qq_id, amount, type, event_id) "
            "VALUES (?,?,?,?,?)",
            (payout_id, qq_id, amount, type_, event_id),
        )

    async def summary_by_date(self, date_str: str) -> list:
        """东八区某日的按 QQ 净额汇总（含冲正负数），qq_id 升序。

        Returns:
            Row 列表，每行含 qq_id、total 两列。
        """
        return await self._db.fetchall(
            "SELECT qq_id, SUM(amount) AS total FROM sync_ledger "
            "WHERE substr(datetime(credited_at,'+8 hours'),1,10)=? "
            "GROUP BY qq_id ORDER BY qq_id",
            (date_str,),
        )
