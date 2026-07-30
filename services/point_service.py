from astrbot.api import logger


class PointService:
    def __init__(self, db, dao):
        self._db = db
        self._dao = dao

    async def add(
        self, qq: str, group_id: str, amount: int,
        reason: str, ref_id: int = None, admin_override: bool = False,
    ) -> dict:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        result = {"qq": qq, "group_id": group_id, "amount": amount, "reason": reason}

        add_to_earned = reason not in ("lottery_cost", "redeem_cost", "admin_sub", "easter_unlucky")
        await self._dao.update_points(qq, group_id, amount, add_to_earned)
        balance = await self._dao.get_user_balance(qq, group_id)
        result["balance"] = balance

        await self._dao.insert_transaction(qq, group_id, amount, balance, reason, ref_id)

        if reason == "admin_add" and not admin_override:
            return result

        if balance >= 0:
            await self._check_and_clear_negative_title(qq, group_id)

        result["title_action"] = None
        return result

    async def subtract(
        self, qq: str, group_id: str, amount: int,
        reason: str, ref_id: int = None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        balance = await self._dao.get_user_balance(qq, group_id)
        if balance < amount:
            raise ValueError(f"Insufficient points: have {balance}, need {amount}")

        result = {"qq": qq, "group_id": group_id, "amount": -amount, "reason": reason}
        await self._dao.update_points(qq, group_id, -amount)
        new_balance = await self._dao.get_user_balance(qq, group_id)
        result["balance"] = new_balance

        await self._dao.insert_transaction(qq, group_id, -amount, new_balance, reason, ref_id)

        title_action = await self._check_and_set_negative_title(qq, group_id, new_balance)
        result["title_action"] = title_action
        return result

    async def get_balance(self, qq: str, group_id: str) -> int:
        return await self._dao.get_user_balance(qq, group_id)

    async def _check_and_set_negative_title(self, qq: str, group_id: str, balance: int):
        if balance >= 0:
            return None
        user = await self._dao.get_user(qq, group_id)
        if user and user["negative_title_id"] is not None:
            return None

        async def _tx(conn):
            cur = await conn.execute(
                "SELECT negative_title_id FROM users WHERE group_id=? AND negative_title_id IS NOT NULL",
                (group_id,),
            )
            rows = await cur.fetchall()
            used = {r[0] for r in rows}
            new_id = 1
            while new_id in used:
                new_id += 1
            await conn.execute(
                "UPDATE users SET negative_title_id=?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (new_id, qq, group_id),
            )
            return new_id

        new_id = await self._db.execute_transaction(_tx)
        logger.info(f"Set negative title for {qq}@{group_id}: 群女仆{new_id}号")
        return new_id

    async def _check_and_clear_negative_title(self, qq: str, group_id: str):
        user = await self._dao.get_user(qq, group_id)
        if user and user["negative_title_id"] is not None:
            await self._dao.set_negative_title(qq, group_id, None)
            logger.info(f"Cleared negative title for {qq}@{group_id}")

    async def is_negative(self, qq: str, group_id: str) -> bool:
        balance = await self.get_balance(qq, group_id)
        return balance < 0
