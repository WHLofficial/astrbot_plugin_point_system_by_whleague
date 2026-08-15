"""打劫业务服务（v0.4.0）。

玩法定稿（见 PLAN_ROB.md §1/§4）：
- 门槛：打劫者积分 ≥ rob_min_points 且非负分；目标积分 ≥ rob_target_min_points 且非负分
- 成本 rob_cost 仅失败时扣除（成功纯收益）；成功率 rob_success_rate
- 收益：stolen = min(rob_reward_cap, rob_reward_fixed + dynamic)，
  dynamic = round(rob_reward_fixed * (target_points / rob_reward_base_points) ** rob_reward_power)
- 防刷：同用户冷却（成功/失败均进入）+ 每日上限（按 QQ 全局）
- 目标防集火：rob_target_limit_dynamic=false（默认）固定上限 rob_target_daily_limit（0=不限）；
  =true 时上限 = rob_target_daily_limit（基准，最小 1）+ 该人今日主动发起打劫次数
  （全部口径，成功与失败均计数）；动态方案基准 0 由配置层拒绝/WebUI 按 1 处理，此处防御兜底
- 原子性：每日限次统计、目标余额读取、收益计算、扣分、记录写入同一事务
"""

import math
import random

from astrbot.api import logger

from ..db.dao import PointDAO
from .point_service import InsufficientPointsError, PointService


class RobError(Exception):
    """打劫业务错误（事务内抛出，execute_transaction 回滚后转为提示文案）。"""


class RobTargetLimitError(RobError):
    """目标被劫上限拦截：打劫未实际执行，不应消耗打劫者冷却。"""


class RobService:
    def __init__(self, db, dao, point_svc, config_cache, rate_limiter):
        self._db = db
        self._dao = dao
        self._point = point_svc
        self._cfg = config_cache
        self._limiter = rate_limiter

    async def rob(self, qq: str, target_qq: str, group_id: str, bot=None) -> dict:
        """执行一次打劫。

        Returns:
            拦截类：{"success": False, "performed": False, "msg": 文案}
            完成类：{"success": 打劫是否成功, "performed": True, "stolen": int,
                     "balance": 打劫者新余额, "target_balance": 目标新余额,
                     "target_qq": str, "cost": int}
        """
        cfg = self._cfg
        if not cfg["rob_enabled"]:
            return {
                "success": False,
                "performed": False,
                "msg": "\u6253\u52ab\u529f\u80fd\u5df2\u5173\u95ed",
            }
        if not group_id:
            return {
                "success": False,
                "performed": False,
                "msg": "\u6253\u52ab\u4ec5\u652f\u6301\u7fa4\u804a",
            }
        if qq == target_qq:
            return {
                "success": False,
                "performed": False,
                "msg": "\u4e0d\u80fd\u6253\u52ab\u81ea\u5df1",
            }

        min_points = cfg["rob_min_points"]
        target_min = cfg["rob_target_min_points"]
        cost = cfg["rob_cost"]

        balance = await self._point.get_balance(qq)
        if balance < 0:
            return {
                "success": False,
                "performed": False,
                "msg": "\u79ef\u5206\u4e3a\u8d1f\uff0c\u65e0\u6cd5\u6253\u52ab",
            }
        if balance < min_points:
            return {
                "success": False,
                "performed": False,
                "msg": f"\u79ef\u5206\u4e0d\u8db3\uff0c\u6253\u52ab\u9700\u8981 \u2265{min_points} \u79ef\u5206\uff08\u5f53\u524d {balance}\uff09",
            }

        target_balance = await self._point.get_balance(target_qq)
        if target_balance < 0:
            return {
                "success": False,
                "performed": False,
                "msg": "\u76ee\u6807\u79ef\u5206\u4e3a\u8d1f\uff0c\u65e0\u6cb9\u6c34\u53ef\u635e",
            }
        if target_balance < target_min:
            return {
                "success": False,
                "performed": False,
                "msg": f"\u76ee\u6807\u79ef\u5206\u4f4e\u4e8e {target_min}\uff0c\u65e0\u6cb9\u6c34\u53ef\u635e",
            }

        cooldown = cfg["rob_cooldown"]
        if cooldown > 0 and not self._limiter.check_user("rob", qq, group_id, cooldown):
            remaining = self._limiter.get_remaining("rob", qq, group_id, cooldown)
            minutes = max(1, math.ceil(remaining / 60))
            return {
                "success": False,
                "performed": False,
                "msg": f"\u6253\u52ab\u51b7\u5374\u4e2d\uff0c\u5269\u4f59 {minutes} \u5206\u949f\u540e\u53ef\u518d\u6b21\u6253\u52ab",
            }

        rate = cfg["rob_success_rate"]
        fixed = cfg["rob_reward_fixed"]
        base = cfg["rob_reward_base_points"]
        power = cfg["rob_reward_power"]
        cap = cfg["rob_reward_cap"]
        daily_limit = cfg["rob_daily_limit"]
        target_daily_limit = cfg["rob_target_daily_limit"]
        target_limit_dynamic = cfg["rob_target_limit_dynamic"]
        decay = cfg["rob_reward_decay"]

        async def _tx(conn):
            # 每日限次：按 QQ 全局统计（跨群共享钱包，与抽奖口径一致）
            if daily_limit > 0:
                cnt = await PointDAO.count_robs_today(conn, qq)
                if cnt >= daily_limit:
                    raise RobError(
                        f"\u4eca\u65e5\u6253\u52ab\u6b21\u6570\u5df2\u8fbe\u4e0a\u9650 ({daily_limit} \u6b21)"
                    )

            # 目标侧防集火：每日被劫上限（全部次数口径，含失败）+
            # 收益衰减计数（仅成功次数），一次查询两用
            total_hits, win_hits = await PointDAO.target_robs_today(conn, target_qq)
            if target_limit_dynamic:
                # 动态方案：上限 = 固定基准值 rob_target_daily_limit（配置层保证 ≥1，
                # max(...,1) 防御兜底）+ 目标今日主动发起打劫次数（全部口径，复用打劫者限次统计）
                own_robs = await PointDAO.count_robs_today(conn, target_qq)
                limit = max(target_daily_limit, 1) + own_robs
                if total_hits >= limit:
                    raise RobTargetLimitError(
                        f"\u76ee\u6807\u4eca\u65e5\u5df2\u88ab\u6253\u52ab {total_hits} \u6b21"
                        f"\uff08\u4eca\u65e5\u4e0a\u9650 {limit}\uff09\uff0c\u65e0\u6cd5\u518d\u88ab\u6253\u52ab"
                    )
            elif target_daily_limit > 0:
                if total_hits >= target_daily_limit:
                    raise RobTargetLimitError(
                        f"\u76ee\u6807\u4eca\u65e5\u5df2\u88ab\u6253\u52ab {total_hits} \u6b21\uff0c\u65e0\u6cd5\u518d\u88ab\u6253\u52ab"
                    )

            # 事务内重取目标余额（与扣分同源，防并发偏差）；
            # 门槛检查后目标可能被并发扣成负，max(..., 0) 防御负值幂运算
            async with conn.execute(
                "SELECT points FROM accounts WHERE qq=?", (target_qq,)
            ) as cur:
                row = await cur.fetchone()
            target_points = max(row["points"] if row else 0, 0)

            success = random.random() < rate
            stolen = 0
            if success:
                dynamic = (
                    round(fixed * (target_points / base) ** power) if base > 0 else 0
                )
                stolen = min(cap, fixed + dynamic)
                # 防集火收益衰减：目标每被成功打劫一次，后续收益递减
                # （仅成功次数计数；目标每日上限关闭时衰减仍生效）
                if decay > 0 and win_hits > 0:
                    stolen = round(stolen * (1 - decay) ** win_hits)
                if stolen > 0:
                    balance = await PointService.change_balance(
                        conn, qq, group_id, stolen, "rob_reward"
                    )
                    target_after = await PointService.change_balance(
                        conn,
                        target_qq,
                        group_id,
                        -stolen,
                        "rob_lost",
                        earned_amount=0,
                    )
                else:
                    # 极端配置（rob_reward_cap/fixed 为 0）下收益为 0：
                    # 跳过 change_balance（amount=0 会被拒），仅记录
                    async with conn.execute(
                        "SELECT points FROM accounts WHERE qq=?", (qq,)
                    ) as cur:
                        row = await cur.fetchone()
                    balance = row["points"] if row else 0
                    target_after = target_points
            else:
                try:
                    balance = await PointService.change_balance(
                        conn,
                        qq,
                        group_id,
                        -cost,
                        "rob_cost",
                        earned_amount=0,
                        guard_balance=cost,
                    )
                except InsufficientPointsError:
                    raise RobError(
                        f"\u79ef\u5206\u4e0d\u8db3\uff0c\u6253\u52ab\u5931\u8d25\u9700\u6263\u9664 {cost} \u79ef\u5206"
                    )
                target_after = target_points

            await PointDAO.insert_rob_record(
                conn, qq, target_qq, group_id, cost, stolen, success
            )
            return success, stolen, balance, target_after

        try:
            success, stolen, balance, target_after = await self._db.execute_transaction(
                _tx
            )
        except RobError as e:
            # 目标被劫上限拦截：打劫未实际执行，清除本次预写入的冷却，
            # 打劫者可立即转打其他目标
            if isinstance(e, RobTargetLimitError):
                self._limiter.clear_user("rob", qq, group_id)
            return {"success": False, "performed": False, "msg": str(e)}

        # 负分联动：失败扣成本可能致负；目标可能被抢成负
        await self._point.ensure_negative_title(qq, group_id, bot=bot)
        await self._point.ensure_negative_title(target_qq, group_id, bot=bot)

        logger.info(
            f"Rob {qq} -> {target_qq}@{group_id}: success={success}, stolen={stolen}"
        )
        return {
            "success": success,
            "performed": True,
            "stolen": stolen,
            "balance": balance,
            "target_balance": target_after,
            "target_qq": target_qq,
            "cost": cost,
        }
