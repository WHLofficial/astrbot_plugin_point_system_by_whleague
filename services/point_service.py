from astrbot.api import logger

from ..utils.group_info import fetch_member_info

# 不计入 total_earned（累计获得）的加分 reason：均为"扣减型"操作（消耗/扣除/负彩蛋），
# 经 add() 传入时必须显式列在此处，否则会虚增累计获得。
_EARNED_EXCLUDED_REASONS = frozenset(
    {"lottery_cost", "redeem_cost", "admin_sub", "easter_unlucky", "redeem_refund"}
)


class InsufficientPointsError(Exception):
    """积分不足（change_balance 事务内守卫触发）。"""


class PointService:
    def __init__(self, db, dao):
        self._db = db
        self._dao = dao

    @staticmethod
    async def change_balance(
        conn,
        qq: str,
        group_id: str,
        amount: int,
        reason: str,
        *,
        earned_amount: int | None = None,
        guard_balance: int | None = None,
        ref_id: int | None = None,
        admin_qq: str | None = None,
    ) -> int:
        """事务内原子改余额并记流水（改分的唯一入口）。

        自动补齐 accounts（全局账户）与 users（群成员关系）行；
        余额以 accounts.points 为准（一号跨群共享）。

        Args:
            conn: 调用方事务传入的连接（禁止在事务外调用）。
            qq: 用户 QQ。
            group_id: 产生该笔变动的群（流水审计维度）。
            amount: 变动值，正为加、负为减。
            reason: 流水原因（reason 编码表）。
            earned_amount: 计入 total_earned 的值；默认 amount（amount<0 时为 0）。
            guard_balance: 非空时执行 points>=guard_balance 守卫，不满足抛
                InsufficientPointsError（防并发透支，如抽奖/兑换扣费）。
            ref_id: 关联记录 ID。
            admin_qq: 操作管理员 QQ（审计）。

        Returns:
            变动后的全局余额。

        Raises:
            ValueError: amount 为 0。
            InsufficientPointsError: 守卫不满足。
        """
        if amount == 0:
            raise ValueError("Amount must not be zero")
        await conn.execute("INSERT OR IGNORE INTO accounts (qq) VALUES (?)", (qq,))
        await conn.execute(
            "INSERT OR IGNORE INTO users (qq, group_id) VALUES (?, ?)", (qq, group_id)
        )
        if earned_amount is None:
            earned_amount = amount if amount > 0 else 0
        if guard_balance is not None:
            async with conn.execute(
                "UPDATE accounts SET points=points+?, updated_at=datetime('now','localtime') "
                "WHERE qq=? AND points>=?",
                (amount, qq, guard_balance),
            ) as cur:
                if cur.rowcount == 0:
                    raise InsufficientPointsError(
                        f"积分不足，需要 {guard_balance} 积分"
                    )
            # 守卫分支（抽奖/兑换扣费等）默认不计累计获得；若确有正收益需
            # 显式传入 earned_amount，此处同事务补记 total_earned。
            if earned_amount and earned_amount > 0:
                await conn.execute(
                    "UPDATE accounts SET total_earned=total_earned+?, "
                    "updated_at=datetime('now','localtime') WHERE qq=?",
                    (earned_amount, qq),
                )
        elif earned_amount:
            await conn.execute(
                "UPDATE accounts SET points=points+?, total_earned=total_earned+?, updated_at=datetime('now','localtime') WHERE qq=?",
                (amount, earned_amount, qq),
            )
        else:
            await conn.execute(
                "UPDATE accounts SET points=points+?, updated_at=datetime('now','localtime') WHERE qq=?",
                (amount, qq),
            )
        async with conn.execute("SELECT points FROM accounts WHERE qq=?", (qq,)) as cur:
            row = await cur.fetchone()
        balance = row[0] if row else 0
        await conn.execute(
            "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason, ref_id, admin_qq) VALUES (?,?,?,?,?,?,?)",
            (qq, group_id, amount, balance, reason, ref_id, admin_qq),
        )
        return balance

    async def add(
        self,
        qq: str,
        group_id: str,
        amount: int,
        reason: str,
        ref_id: int | None = None,
        admin_override: bool = False,
        admin_qq: str | None = None,
        bot=None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        result = {"qq": qq, "group_id": group_id, "amount": amount, "reason": reason}

        earned_amount = amount if reason not in _EARNED_EXCLUDED_REASONS else 0

        async def _tx(conn):
            return await self.change_balance(
                conn,
                qq,
                group_id,
                amount,
                reason,
                earned_amount=earned_amount,
                ref_id=ref_id,
                admin_qq=admin_qq,
            )

        balance = await self._db.execute_transaction(_tx)
        result["balance"] = balance

        if reason == "admin_add" and not admin_override:
            return result

        title_action = await self.ensure_negative_title(qq, group_id, bot=bot)
        result["title_action"] = title_action
        return result

    async def subtract(
        self,
        qq: str,
        group_id: str,
        amount: int,
        reason: str,
        ref_id: int | None = None,
        admin_qq: str | None = None,
        bot=None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        result = {"qq": qq, "group_id": group_id, "amount": -amount, "reason": reason}

        async def _tx(conn):
            # 允许扣成负数（管理员惩罚场景），扣负后由 ensure_negative_title 补发负分头衔
            return await self.change_balance(
                conn,
                qq,
                group_id,
                -amount,
                reason,
                earned_amount=0,
                ref_id=ref_id,
                admin_qq=admin_qq,
            )

        new_balance = await self._db.execute_transaction(_tx)
        result["balance"] = new_balance

        title_action = await self.ensure_negative_title(qq, group_id, bot=bot)
        result["title_action"] = title_action
        return result

    async def get_balance(self, qq: str) -> int:
        return await self._dao.get_balance(qq)

    async def ensure_negative_title(self, qq: str, group_id: str, bot=None):
        """余额为负且尚无头衔时，分配并应用「群女仆X号」头衔；余额回正时在所有群移除头衔并恢复原群名片。

        Args:
            qq: 用户 QQ。
            group_id: 触发本次检查的群 ID（负分时在此群懒加载头衔）。
            bot: 平台 bot 实例（aiocqhttp），用于实际调用 set_group_card；
                无 bot（如定时任务上下文）时仅维护 DB 状态，下次有 bot 的流程会补齐。

        Returns:
            新头衔编号（设置时）；清除时返回 None。
        """
        balance = await self.get_balance(qq)
        if balance < 0:
            return await self._check_and_set_negative_title(
                qq, group_id, balance, bot=bot
            )
        await self._clear_negative_titles(qq, bot=bot)
        return None

    async def _check_and_set_negative_title(
        self, qq: str, group_id: str, balance: int, bot=None
    ):
        if balance >= 0:
            return None
        user = await self._dao.get_user(qq, group_id)
        if user and user["negative_title_id"] is not None:
            return None

        info = await fetch_member_info(bot, qq, group_id)
        prev_card = (info or {}).get("card")

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
            await self._set_group_card(bot, qq, group_id, f"群女仆{new_id}号")
        logger.info(f"Set negative title for {qq}@{group_id}: 群女仆{new_id}号")
        return new_id

    async def _clear_negative_titles(self, qq: str, bot=None):
        """余额回正后，清除用户在全部群的负分头衔并恢复原群名片（跨群联动）。"""
        for gid in await self._dao.get_user_groups(qq):
            user = await self._dao.get_user(qq, gid)
            if not user or user["negative_title_id"] is None:
                continue
            prev_card = user["negative_title_prev_card"]
            await self._dao.set_negative_title(qq, gid, None)
            if bot is not None:
                await self._set_group_card(bot, qq, gid, prev_card or "")
            logger.info(f"Cleared negative title for {qq}@{gid}")

    async def is_negative(self, qq: str) -> bool:
        balance = await self.get_balance(qq)
        return balance < 0

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
