import random
from datetime import datetime
from astrbot.api import logger
from ..utils.helpers import today_str, today_mmdd
from ..utils.fortune import format_fortune


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

    async def sign_in(self, qq: str, group_id: str, platform: str, message: str, bot=None):
        today = today_str()
        mmdd = today_mmdd()

        user = await self._dao.ensure_user(qq, group_id, platform)
        if user["last_sign_date"] == today:
            return {"already_signed": True, "msg": "\u4eca\u5929\u5df2\u7ecf\u7b7e\u5230\u8fc7\u4e86\uff01"}

        last_date = user["last_sign_date"]
        consecutive = user["consecutive_days"]
        total_days = user["total_sign_days"]

        if last_date:
            try:
                diff = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last_date, "%Y-%m-%d")).days
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

        cap = cfg["signin_consecutive_max"]
        effective = min(consecutive, cap)
        bonus_consecutive = effective * cfg["signin_consecutive_bonus_per_day"]
        total_points += bonus_consecutive

        if consecutive > 0 and consecutive % 7 == 0:
            bonus_weekly = cfg["signin_weekly_bonus"]
            total_points += bonus_weekly

        easter = await self._easter.trigger(
            qq, group_id, user["lucky_pity"], user["unlucky_pity"]
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
        if user["birthday"] == mmdd and str(user["birthday_year"] or "") != current_year:
            birthday_bonus = cfg["birthday_bonus_points"]
            total_points += birthday_bonus

        async def _tx(conn):
            # 事务内二次查重：并发签到只能有一个成功
            cur = await conn.execute(
                "SELECT 1 FROM sign_in_log WHERE qq=? AND group_id=? AND sign_date=?",
                (qq, group_id, today),
            )
            if await cur.fetchone():
                raise AlreadySigned()
            # 每日首签判定移入事务：并发时只有真正第一个签到者获得奖励
            cur = await conn.execute(
                "SELECT COUNT(*) AS cnt FROM sign_in_log WHERE group_id=? AND sign_date=?",
                (group_id, today),
            )
            row = await cur.fetchone()
            first_bonus = cfg["signin_day_first_bonus"] if row and row[0] == 0 else 0
            granted = total_points + first_bonus
            await conn.execute(
                "INSERT INTO sign_in_log (qq, group_id, sign_date, points_earned, base_points, bonus_first_sign, bonus_day_first, bonus_consecutive, bonus_weekly, easter_event_type, easter_points) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (qq, group_id, today, granted, base_points, bonus_first, first_bonus, bonus_consecutive, bonus_weekly, easter_type, easter_points),
            )
            await conn.execute(
                "UPDATE users SET last_sign_date=?, consecutive_days=?, total_sign_days=?, max_consecutive_days=MAX(max_consecutive_days,?), lucky_pity=?, unlucky_pity=?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (today, consecutive, total_days, consecutive, new_lucky_pity, new_unlucky_pity, qq, group_id),
            )
            # 非酋负事件只扣余额、不计入累计获得
            earned_inc = granted - min(easter_points, 0)
            await conn.execute(
                "UPDATE users SET points=points+?, total_earned=total_earned+?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (granted, earned_inc, qq, group_id),
            )
            if birthday_bonus:
                await conn.execute(
                    "UPDATE users SET birthday_year=? WHERE qq=? AND group_id=?",
                    (current_year, qq, group_id),
                )
            cur = await conn.execute("SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id))
            row = await cur.fetchone()
            balance = row[0] if row else granted
            await conn.execute(
                "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)",
                (qq, group_id, granted, balance, "签到"),
            )
            return granted, first_bonus

        try:
            granted, bonus_day_first = await self._db.execute_transaction(_tx)
        except AlreadySigned:
            return {"already_signed": True, "msg": "\u4eca\u5929\u5df2\u7ecf\u7b7e\u5230\u8fc7\u4e86\uff01"}

        # 彩蛋非酋可能把余额打成负分，补发负分头衔；回正时自动移除
        await self._point.ensure_negative_title(qq, group_id, bot=bot)

        parts = [
            f"\u2705 \u7b7e\u5230\u6210\u529f\uff01\u83b7\u5f97 +{granted} \u79ef\u5206",
            f"  \xb7 \u57fa\u7840\u5206: {base_points}",
        ]
        if bonus_first:
            parts.append(f"  \xb7 \u9996\u6b21\u7b7e\u5230\u5956\u52b1: +{bonus_first}")
        if bonus_day_first:
            parts.append(f"  \xb7 \u6bcf\u65e5\u9996\u7b7e\u5956\u52b1: +{bonus_day_first}")
        if bonus_consecutive:
            parts.append(f"  \xb7 \u8fde\u7b7e\u5956\u52b1(\u7b2c{consecutive}\u5929): +{bonus_consecutive}")
        if bonus_weekly:
            parts.append(f"  \xb7 \u6bcf7\u5929\u5956\u52b1: +{bonus_weekly}")
        if easter_result and easter_points > 0:
            parts.append(f"  \xb7 \U00002728 {easter_result['name']}: +{easter_points}")
        if easter_result and easter_points < 0:
            parts.append(f"  \xb7 \U0001f4a0 {easter_result['name']}: {easter_points}")
        if date_reward_pts:
            parts.append(f"  \xb7 \u65e5\u671f\u53e3\u4ee4\u5956\u52b1: +{date_reward_pts}")
        if birthday_bonus:
            parts.append(f"  \xb7 \U0001f382 \u751f\u65e5\u5956\u52b1: +{birthday_bonus}")

        user_name = qq
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
