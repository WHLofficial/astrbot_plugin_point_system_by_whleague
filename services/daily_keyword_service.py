from astrbot.api import logger

from ..utils.helpers import period_start_str, today_str
from .point_service import PointService


class AlreadyClaimed(Exception):
    """并发下重复领取口令，用于回滚事务。"""


class BlockedClaim(Exception):
    """负分用户领取口令被拦截，用于回滚事务。"""


class DailyKeywordService:
    def __init__(self, db, dao, point_svc):
        self._db = db
        self._dao = dao
        self._point = point_svc
        self._cache = {}

    async def _get_kw(self, group_id: str, today: str):
        """按 (群, 日期) 缓存当日口令，避免每条群消息都查库。"""
        if len(self._cache) > 256:
            stale = [k for k in self._cache if k[1] != today]
            for k in stale:
                del self._cache[k]
        key = (group_id, today)
        if key not in self._cache:
            self._cache[key] = await self._dao.get_daily_keyword(group_id, today)
        return self._cache[key]

    def invalidate(self, group_id: str):
        """管理员设置/清除口令后调用，使当日缓存失效。"""
        self._cache.pop((group_id, today_str()), None)

    async def check_and_claim(
        self, qq: str, group_id: str, message: str, bot=None
    ) -> dict:
        today = today_str()
        kw_record = await self._get_kw(group_id, today)
        if not kw_record:
            return {"claimed": False}

        kw = kw_record["keyword"]
        if kw.lower() not in message.lower():
            return {"claimed": False}

        already = await self._dao.has_claimed_daily_keyword(kw_record["id"], qq)
        if already:
            return {"claimed": False, "already": True}

        points = kw_record["points"]
        is_neg = await self._point.is_negative(qq)
        if is_neg:
            return {"claimed": False, "blocked": True}

        async def _tx(conn):
            # 全局限领 1 次（v0.2.1）：同用户当天在任意群已领取过口令则拒绝，
            # 与口令内容、群无关；事务内查重 + BEGIN IMMEDIATE 串行化防并发
            async with conn.execute(
                "SELECT 1 FROM daily_keyword_claim WHERE qq=? AND created_at>=? LIMIT 1",
                (qq, period_start_str()),
            ) as cur:
                if await cur.fetchone():
                    raise AlreadyClaimed()
            # 事务内以 rowcount 判定，防止并发重复领取（同群同口令）
            async with conn.execute(
                "INSERT OR IGNORE INTO daily_keyword_claim (kw_id, qq, group_id, points_earned) VALUES (?,?,?,?)",
                (kw_record["id"], qq, group_id, points),
            ) as cur:
                if cur.rowcount == 0:
                    raise AlreadyClaimed()
            # 事务内负分拦截：与事务外快速检查互补，消除竞态窗口
            async with conn.execute(
                "SELECT points FROM accounts WHERE qq=?", (qq,)
            ) as cur:
                row = await cur.fetchone()
            if row and row["points"] < 0:
                raise BlockedClaim()
            # 未签到过的用户无账户行，change_balance 会补建（accounts + 群成员行）
            await PointService.change_balance(
                conn, qq, group_id, points, "daily_keyword"
            )

        try:
            await self._db.execute_transaction(_tx)
        except AlreadyClaimed:
            return {"claimed": False, "already": True}
        except BlockedClaim:
            return {"claimed": False, "blocked": True}

        await self._point.ensure_negative_title(qq, group_id, bot=bot)

        logger.info(f"Daily keyword claimed by {qq}@{group_id}: +{points}")
        return {"claimed": True, "points": points, "keyword": kw}
