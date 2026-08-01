
from astrbot.api import logger

# 不计入 total_earned（累计获得）的加分 reason：均为"扣减型"操作（消耗/扣除/负彩蛋），
# 经 add() 传入时必须显式列在此处，否则会虚增累计获得。
_EARNED_EXCLUDED_REASONS = frozenset(
    {"lottery_cost", "redeem_cost", "admin_sub", "easter_unlucky"}
)


class PointService:
    def __init__(self, db, dao):
        self._db = db
        self._dao = dao

    async def add(
        self, qq: str, group_id: str, amount: int,
        reason: str, ref_id: int | None = None, admin_override: bool = False,
        admin_qq: str | None = None, bot=None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        result = {"qq": qq, "group_id": group_id, "amount": amount, "reason": reason}

        add_to_earned = reason not in _EARNED_EXCLUDED_REASONS

        async def _tx(conn):
            # 从未签到过的用户无 users 行，先补建再加分，防止积分丢失
            await conn.execute(
                "INSERT OR IGNORE INTO users (qq, group_id) VALUES (?, ?)",
                (qq, group_id),
            )
            if add_to_earned:
                await conn.execute(
                    "UPDATE users SET points=points+?, total_earned=total_earned+?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                    (amount, amount, qq, group_id),
                )
            else:
                await conn.execute(
                    "UPDATE users SET points=points+?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                    (amount, qq, group_id),
                )
            async with conn.execute("SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id)) as cur:
                row = await cur.fetchone()
            balance = row[0] if row else 0
            await conn.execute(
                "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason, ref_id, admin_qq) VALUES (?,?,?,?,?,?,?)",
                (qq, group_id, amount, balance, reason, ref_id, admin_qq),
            )
            return balance

        balance = await self._db.execute_transaction(_tx)
        result["balance"] = balance

        if reason == "admin_add" and not admin_override:
            return result

        title_action = await self.ensure_negative_title(qq, group_id, bot=bot)
        result["title_action"] = title_action
        return result

    async def subtract(
        self, qq: str, group_id: str, amount: int,
        reason: str, ref_id: int | None = None,
        admin_qq: str | None = None, bot=None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        result = {"qq": qq, "group_id": group_id, "amount": -amount, "reason": reason}

        async def _tx(conn):
            # 从未签到过的用户无 users 行，先补建行，避免扣分静默丢失
            await conn.execute(
                "INSERT OR IGNORE INTO users (qq, group_id) VALUES (?, ?)",
                (qq, group_id),
            )
            async with conn.execute(
                "SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id)
            ) as cur:
                row = await cur.fetchone()
            balance = row[0] if row else 0
            # 允许扣成负数（管理员惩罚场景），扣负后由 ensure_negative_title 补发负分头衔
            new_balance = balance - amount
            await conn.execute(
                "UPDATE users SET points=points-?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (amount, qq, group_id),
            )
            await conn.execute(
                "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason, ref_id, admin_qq) VALUES (?,?,?,?,?,?,?)",
                (qq, group_id, -amount, new_balance, reason, ref_id, admin_qq),
            )
            return new_balance

        new_balance = await self._db.execute_transaction(_tx)
        result["balance"] = new_balance

        title_action = await self.ensure_negative_title(qq, group_id, bot=bot)
        result["title_action"] = title_action
        return result

    async def get_balance(self, qq: str, group_id: str) -> int:
        return await self._dao.get_user_balance(qq, group_id)

    async def ensure_negative_title(self, qq: str, group_id: str, bot=None):
        """余额为负且尚无头衔时，分配并应用「群女仆X号」头衔；余额回正时移除并恢复原群名片。

        Args:
            qq: 用户 QQ。
            group_id: 群 ID。
            bot: 平台 bot 实例（aiocqhttp），用于实际调用 set_group_card；
                无 bot（如定时任务上下文）时仅维护 DB 状态，下次有 bot 的流程会补齐。

        Returns:
            新头衔编号（设置时）；清除时返回 None。
        """
        balance = await self.get_balance(qq, group_id)
        if balance < 0:
            return await self._check_and_set_negative_title(qq, group_id, balance, bot=bot)
        await self._check_and_clear_negative_title(qq, group_id, bot=bot)
        return None

    async def _check_and_set_negative_title(self, qq: str, group_id: str, balance: int, bot=None):
        if balance >= 0:
            return None
        user = await self._dao.get_user(qq, group_id)
        if user and user["negative_title_id"] is not None:
            return None

        prev_card = await self._fetch_group_card(bot, qq, group_id)

        async def _tx(conn):
            async with conn.execute(
                "SELECT negative_title_id FROM users WHERE group_id=? AND negative_title_id IS NOT NULL",
                (group_id,),
            ) as cur:
                rows = await cur.fetchall()
            used = {r[0] for r in rows}
            new_id = 1
            while new_id in used:
                new_id += 1
            await conn.execute(
                "UPDATE users SET negative_title_id=?, negative_title_prev_card=?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (new_id, prev_card, qq, group_id),
            )
            return new_id

        new_id = await self._db.execute_transaction(_tx)
        if bot is not None:
            await self._set_group_card(bot, qq, group_id, f"\u7fa4\u5973\u4ec6{new_id}\u53f7")
        logger.info(f"Set negative title for {qq}@{group_id}: \u7fa4\u5973\u4ec6{new_id}\u53f7")
        return new_id

    async def _check_and_clear_negative_title(self, qq: str, group_id: str, bot=None):
        user = await self._dao.get_user(qq, group_id)
        if not user or user["negative_title_id"] is None:
            return
        prev_card = user["negative_title_prev_card"]
        await self._dao.set_negative_title(qq, group_id, None)
        if bot is not None:
            await self._set_group_card(bot, qq, group_id, prev_card or "")
        logger.info(f"Cleared negative title for {qq}@{group_id}")

    async def is_negative(self, qq: str, group_id: str) -> bool:
        balance = await self.get_balance(qq, group_id)
        return balance < 0

    async def _fetch_group_card(self, bot, qq: str, group_id: str) -> str | None:
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
                return info.get("card")
        except Exception as e:
            logger.warning(f"Failed to fetch group card for {qq}@{group_id}: {e}")
        return None

    async def _set_group_card(self, bot, qq: str, group_id: str, card: str):
        call = getattr(bot, "call_action", None)
        if not call:
            return
        try:
            await call(
                action="set_group_card",
                group_id=int(group_id),
                user_id=int(qq),
                card=card,
            )
        except Exception as e:
            logger.warning(f"Failed to set group card for {qq}@{group_id}: {e}")
