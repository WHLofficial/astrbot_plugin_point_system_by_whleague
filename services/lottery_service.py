import json
import random

from astrbot.api import logger

from ..utils.helpers import period_start_str
from .point_service import InsufficientPointsError, PointService


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
            return {"success": False, "msg": "抽奖功能已关闭"}

        cost = self._cfg["lottery_cost"]
        balance = await self._point.get_balance(qq)
        if balance < cost:
            return {
                "success": False,
                "msg": f"积分不足，需要 {cost} 积分，当前 {balance}",
            }

        if self._cfg["negative_disable_lottery"]:
            is_neg = await self._point.is_negative(qq)
            if is_neg:
                return {"success": False, "msg": "积分为负，无法抽奖，请先签到恢复积分"}

        raw = self._cfg["lottery_tiers"]
        try:
            if isinstance(raw, str):
                data = json.loads(raw)
            else:
                data = raw
            tiers = data["tiers"]
        except (TypeError, ValueError, KeyError) as e:
            logger.error(f"Invalid lottery_tiers config: {e}")
            return {
                "success": False,
                "msg": "抽奖档位配置异常，请联系管理员",
            }
        if not tiers:
            return {
                "success": False,
                "msg": "\u62bd\u5956\u6863\u4f4d\u672a\u914d\u7f6e",
            }

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

        reward = random.randint(int(chosen["points_min"]), int(chosen["points_max"]))
        is_win = reward > 0

        daily_limit = self._cfg["lottery_daily_limit"]

        async def _tx(conn):
            # 每日限次按 QQ 全局统计（v0.2.1：跨群共享钱包，各群不能分开刷满）
            if daily_limit > 0:
                async with conn.execute(
                    "SELECT COUNT(*) AS cnt FROM lottery_record WHERE qq=? AND created_at>=?",
                    (qq, period_start_str()),
                ) as cur:
                    row = await cur.fetchone()
                if row and row[0] >= daily_limit:
                    raise LotteryError(f"今日抽奖次数已达上限 ({daily_limit} 次)")
            # 余额守卫在事务内生效，防止并发抽奖透支
            try:
                balance = await PointService.change_balance(
                    conn,
                    qq,
                    group_id,
                    -cost,
                    "lottery_cost",
                    earned_amount=0,
                    guard_balance=cost,
                )
            except InsufficientPointsError:
                raise LotteryError(f"积分不足，需要 {cost} 积分")
            if reward > 0:
                balance = await PointService.change_balance(
                    conn, qq, group_id, reward, "lottery_reward"
                )
            await conn.execute(
                "INSERT INTO lottery_record (qq, group_id, cost, reward_amount, is_win, tier_label) VALUES (?,?,?,?,?,?)",
                (qq, group_id, cost, reward, 1 if is_win else 0, chosen["label"]),
            )
            return balance

        try:
            balance = await self._db.execute_transaction(_tx)
        except LotteryError as e:
            return {"success": False, "msg": str(e)}

        await self._point.ensure_negative_title(qq, group_id, bot=bot)

        logger.info(
            f"Lottery {qq}@{group_id}: {chosen['label']}, cost={cost}, reward={reward}"
        )
        emoji = chosen.get("emoji", "")
        delta = reward - cost
        lines = [
            f"{emoji} {chosen['label']}",
            f"  · 消耗: {cost} 积分",
        ]
        if is_win:
            lines.append(f"  · 获得: +{reward} 积分")
        else:
            lines.append("  · 未中奖")
        lines.append(f"  · 积分变化: {delta:+d}")
        lines.append(f"  · 当前积分: {balance}")
        return {
            "success": True,
            "tier": chosen["label"],
            "emoji": emoji,
            "cost": cost,
            "reward": reward,
            "is_win": is_win,
            "balance": balance,
            "msg": "\n".join(lines),
        }
