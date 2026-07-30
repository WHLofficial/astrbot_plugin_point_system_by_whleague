from astrbot.api import logger
from utils.helpers import today_str


class DailyKeywordService:
    def __init__(self, db, dao, point_svc):
        self._db = db
        self._dao = dao
        self._point = point_svc

    async def check_and_claim(self, qq: str, group_id: str, message: str) -> dict:
        today = today_str()
        kw_record = await self._dao.get_daily_keyword(group_id, today)
        if not kw_record:
            return {"claimed": False}

        kw = kw_record["keyword"]
        if kw.lower() not in message.lower():
            return {"claimed": False}

        already = await self._dao.has_claimed_daily_keyword(kw_record["id"], qq)
        if already:
            return {"claimed": False, "already": True}

        points = kw_record["points"]
        is_neg = await self._point.is_negative(qq, group_id)
        if is_neg:
            return {"claimed": False, "blocked": True}

        async def _tx(conn):
            await conn.execute("INSERT OR IGNORE INTO daily_keyword_claim (kw_id, qq, group_id, points_earned) VALUES (?,?,?,?)", (kw_record["id"], qq, group_id, points))
            await conn.execute("UPDATE users SET points=points+?, total_earned=total_earned+?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?", (points, points, qq, group_id))
            cur = await conn.execute("SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id))
            bal = (await cur.fetchone())[0]
            await conn.execute("INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)", (qq, group_id, points, bal, "daily_keyword"))

        await self._db.execute_transaction(_tx)

        logger.info(f"Daily keyword claimed by {qq}@{group_id}: +{points}")
        return {"claimed": True, "points": points, "keyword": kw}
