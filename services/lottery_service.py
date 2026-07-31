import random
import json
from datetime import date
from astrbot.api import logger


class LotteryError(Exception):
    """抽奖业务异常（余额不足/次数上限），消息随异常返回用户。"""


class LotteryService:
    def __init__(self, db, dao, point_svc, config_cache):
        self._db = db
        self._dao = dao
        self._point = point_svc
        self._cfg = config_cache

    async def draw(self, qq: str, group_id: str, bot=None) -> dict:
        if not self._cfg["lottery_enabled"]:
            return {"success": False, "msg": "\u62bd\u5956\u529f\u80fd\u5df2\u5173\u95ed"}

        cost = self._cfg["lottery_cost"]
        balance = await self._point.get_balance(qq, group_id)
        if balance < cost:
            return {"success": False, "msg": f"\u79ef\u5206\u4e0d\u8db3\uff0c\u9700\u8981 {cost} \u79ef\u5206\uff0c\u5f53\u524d {balance}"}

        if self._cfg["negative_disable_lottery"]:
            is_neg = await self._point.is_negative(qq, group_id)
            if is_neg:
                return {"success": False, "msg": "\u79ef\u5206\u4e3a\u8d1f\uff0c\u65e0\u6cd5\u62bd\u5956\uff0c\u8bf7\u5148\u7b7e\u5230\u6062\u590d\u79ef\u5206"}

        raw = self._cfg["lottery_tiers"]
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw
        tiers = data["tiers"]
        if not tiers:
            return {"success": False, "msg": "\u62bd\u5956\u6863\u4f4d\u672a\u914d\u7f6e"}

        weights = [t["weight"] for t in tiers]
        total_weight = sum(weights)
        r = random.uniform(0, total_weight)
        cumulative = 0.0
        chosen = tiers[0]
        for t in tiers:
            cumulative += t["weight"]
            if r < cumulative:
                chosen = t
                break

        reward = int(cost * chosen["multiplier"])
        is_win = reward > 0

        today = date.today().isoformat()
        daily_limit = self._cfg["lottery_daily_limit"]

        async def _tx(conn):
            if daily_limit > 0:
                cur = await conn.execute(
                    "SELECT COUNT(*) AS cnt FROM lottery_record WHERE qq=? AND group_id=? AND date(created_at)=?",
                    (qq, group_id, today),
                )
                row = await cur.fetchone()
                if row and row[0] >= daily_limit:
                    raise LotteryError(f"\u4eca\u65e5\u62bd\u5956\u6b21\u6570\u5df2\u8fbe\u4e0a\u9650 ({daily_limit} \u6b21)")
            # 余额守卫在事务内生效，防止并发抽奖透支
            cur = await conn.execute(
                "UPDATE users SET points=points-? WHERE qq=? AND group_id=? AND points>=?",
                (cost, qq, group_id, cost),
            )
            if cur.rowcount == 0:
                raise LotteryError(f"\u79ef\u5206\u4e0d\u8db3\uff0c\u9700\u8981 {cost} \u79ef\u5206")
            cur = await conn.execute("SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id))
            bal1 = (await cur.fetchone())[0]
            await conn.execute("INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)", (qq, group_id, -cost, bal1, "lottery_cost"))
            if reward > 0:
                await conn.execute("UPDATE users SET points=points+?, total_earned=total_earned+? WHERE qq=? AND group_id=?", (reward, reward, qq, group_id))
                cur2 = await conn.execute("SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id))
                bal2 = (await cur2.fetchone())[0]
                await conn.execute("INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)", (qq, group_id, reward, bal2, "lottery_reward"))
            await conn.execute("INSERT INTO lottery_record (qq, group_id, cost, reward_amount, is_win, tier_label) VALUES (?,?,?,?,?,?)", (qq, group_id, cost, reward, 1 if is_win else 0, chosen["label"]))

        try:
            await self._db.execute_transaction(_tx)
        except LotteryError as e:
            return {"success": False, "msg": str(e)}

        await self._point.ensure_negative_title(qq, group_id, bot=bot)

        logger.info(f"Lottery {qq}@{group_id}: {chosen['label']}, cost={cost}, reward={reward}")
        emoji = chosen.get("emoji", "")
        return {
            "success": True,
            "tier": chosen["label"],
            "emoji": emoji,
            "cost": cost,
            "reward": reward,
            "is_win": is_win,
            "msg": f"{emoji} {chosen['label']}\n\u83b7\u5f97 {reward} \u79ef\u5206" if is_win else f"{emoji} {chosen['label']}\n\u8d39\u7528 {cost} \u79ef\u5206\uff0c\u672a\u4e2d\u5956",
        }
