import random
from datetime import datetime

from ..utils.fortune import format_fortune
from ..utils.helpers import today_mmdd, today_str
from .point_service import PointService


class AlreadySigned(Exception):
    """并发下重复签到标记，用于回滚事务。"""


class SignInService:
    def __init__(self, db, dao, point_svc, easter_svc, date_reward_svc, config_cache):
        self._db = db
        self._dao = dao
        self._point = point_svc
        self._easter = easter_svc
        self._date_reward = date_reward_svc
        self._cfg = config_cache

    async def sign_in(
        self,
        qq: str,
        group_id: str,
        platform: str,
        message: str,
        bot=None,
        user_name: str | None = None,
    ):
        today = today_str()
        mmdd = today_mmdd()

        # 签到状态按 QQ 全局（一号跨群共享：每天全局限签 1 次）。
        # ensure_user 内部已确保 accounts 行存在，这里只需一次读取
        await self._dao.ensure_user(qq, group_id, platform)
        account = await self._dao.get_account(qq)
        if account["last_sign_date"] == today:
            return {"already_signed": True, "msg": "今天已经签到过了！"}

        last_date = account["last_sign_date"]
        consecutive = account["consecutive_days"]
        total_days = account["total_sign_days"]

        if last_date:
            try:
                diff = (
                    datetime.strptime(today, "%Y-%m-%d")
                    - datetime.strptime(last_date, "%Y-%m-%d")
                ).days
                consecutive = consecutive + 1 if diff == 1 else 1
            except (ValueError, TypeError):
                consecutive = 1
        else:
            consecutive = 1
        total_days += 1

        cfg = self._cfg

        if cfg["signin_fixed_mode"]:
            base_points = cfg["signin_fixed_points"]
        else:
            lo = min(cfg["signin_random_min"], cfg["signin_random_max"])
            hi = max(cfg["signin_random_min"], cfg["signin_random_max"])
            base_points = random.randint(lo, hi)

        total_points = base_points
        bonus_first = 0
        bonus_day_first = 0
        bonus_consecutive = 0
        bonus_weekly = 0

        if total_days == 1:
            bonus_first = cfg["signin_first_bonus"]
            total_points += bonus_first

        # 连签奖励从第 2 天起算：第 N 天 = (N-1) × per_day，第 1 天为 0
        cap = cfg["signin_consecutive_max"]
        effective = min(consecutive, cap)
        bonus_consecutive = (
            max(0, effective - 1) * cfg["signin_consecutive_bonus_per_day"]
        )
        total_points += bonus_consecutive

        if consecutive > 0 and consecutive % 7 == 0:
            bonus_weekly = cfg["signin_weekly_bonus"]
            total_points += bonus_weekly

        easter = await self._easter.trigger(
            qq, group_id, account["lucky_pity"], account["unlucky_pity"]
        )
        easter_result = easter["event"]
        new_lucky_pity = easter["lucky_pity"]
        new_unlucky_pity = easter["unlucky_pity"]
        easter_type = None
        easter_points = 0
        if easter_result:
            easter_type = easter_result["event_type"]
            easter_points = easter_result["points"]
            total_points += easter_points

        date_reward_pts = await self._date_reward.check(mmdd, message)
        total_points += date_reward_pts

        birthday_bonus = 0
        current_year = today[:4]
        if (
            account["birthday"] == mmdd
            and str(account["birthday_year"] or "") != current_year
        ):
            birthday_bonus = cfg["birthday_bonus_points"]
            total_points += birthday_bonus

        async def _tx(conn):
            # 事务内二次查重（全局限签）：并发签到只能有一个成功
            async with conn.execute(
                "SELECT 1 FROM sign_in_log WHERE qq=? AND sign_date=?",
                (qq, today),
            ) as cur:
                if await cur.fetchone():
                    raise AlreadySigned()
            # 每日首签判定按群：并发时只有真正第一个签到者获得奖励
            async with conn.execute(
                "SELECT COUNT(*) AS cnt FROM sign_in_log WHERE group_id=? AND sign_date=?",
                (group_id, today),
            ) as cur:
                row = await cur.fetchone()
                first_bonus = (
                    cfg["signin_day_first_bonus"] if row and row[0] == 0 else 0
                )
            granted = total_points + first_bonus
            # 更新全局签到状态（accounts）
            await conn.execute(
                "UPDATE accounts SET last_sign_date=?, consecutive_days=?, total_sign_days=?, "
                "lucky_pity=?, unlucky_pity=?, updated_at=datetime('now','localtime') WHERE qq=?",
                (today, consecutive, total_days, new_lucky_pity, new_unlucky_pity, qq),
            )
            if birthday_bonus:
                await conn.execute(
                    "UPDATE accounts SET birthday_year=?, updated_at=datetime('now','localtime') WHERE qq=?",
                    (current_year, qq),
                )
            # 保持群成员最近活跃时间（全局榜"最近活跃群"归属依据）
            await conn.execute(
                "UPDATE users SET updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (qq, group_id),
            )
            # 非酋负事件只扣余额、不计入累计获得
            earned_inc = granted - min(easter_points, 0)
            # 先写签到日志拿到 id，流水 ref_id 关联（审计闭环）
            cur = await conn.execute(
                "INSERT INTO sign_in_log (qq, group_id, sign_date, points_earned, base_points, bonus_first_sign, bonus_day_first, bonus_consecutive, bonus_weekly, easter_event_type, easter_points) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    qq,
                    group_id,
                    today,
                    granted,
                    base_points,
                    bonus_first,
                    first_bonus,
                    bonus_consecutive,
                    bonus_weekly,
                    easter_type,
                    easter_points,
                ),
            )
            try:
                sign_in_log_id = cur.lastrowid
            finally:
                await cur.close()
            await PointService.change_balance(
                conn,
                qq,
                group_id,
                granted,
                "签到",
                earned_amount=earned_inc,
                ref_id=sign_in_log_id,
            )
            return granted, first_bonus

        try:
            granted, bonus_day_first = await self._db.execute_transaction(_tx)
        except AlreadySigned:
            return {"already_signed": True, "msg": "今天已经签到过了！"}

        # 彩蛋非酋可能把余额打成负分，补发负分头衔；回正时自动移除
        await self._point.ensure_negative_title(qq, group_id, bot=bot)

        parts = [
            f"✅ 签到成功！获得 {granted:+d} 积分",
            f"  · 基础分: {base_points}",
        ]
        if bonus_first:
            parts.append(f"  · 首次签到奖励: +{bonus_first}")
        if bonus_day_first:
            parts.append(f"  · 每日首签奖励: +{bonus_day_first}")
        if bonus_consecutive:
            parts.append(f"  · 连签奖励(第{consecutive}天): +{bonus_consecutive}")
        if bonus_weekly:
            parts.append(f"  · 每7天奖励: +{bonus_weekly}")
        if easter_result and easter_points > 0:
            parts.append(f"  · ✨ {easter_result['name']}: +{easter_points}")
        if easter_result and easter_points < 0:
            parts.append(f"  · 💠 {easter_result['name']}: {easter_points}")
        if date_reward_pts:
            parts.append(f"  · 日期口令奖励: +{date_reward_pts}")
        if birthday_bonus:
            parts.append(f"  · 🎂 生日奖励: +{birthday_bonus}")

        user_name = user_name or qq
        fortune_text = format_fortune(qq, today, user_name)

        parts.append(fortune_text)

        return {
            "already_signed": False,
            "points": granted,
            "consecutive": consecutive,
            "msg": "\n".join(parts),
        }

    async def get_stats(self, group_id: str) -> dict:
        today = today_str()
        total = await self._dao.count_users_in_group(group_id)
        today_count = await self._dao.count_signins_today(group_id, today)
        first_signer = await self._dao.get_first_signer_today(group_id, today)
        streak_king = await self._dao.get_max_streak_today(group_id)

        rate = f"{today_count / total * 100:.1f}%" if total > 0 else "0%"
        return {
            "total": total,
            "today_count": today_count,
            "rate": rate,
            "first_signer_qq": first_signer["qq"] if first_signer else None,
            "streak_king_qq": streak_king["qq"] if streak_king else None,
            "streak_days": streak_king["consecutive_days"] if streak_king else 0,
        }
