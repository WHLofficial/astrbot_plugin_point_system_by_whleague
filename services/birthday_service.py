from ..utils.helpers import today_mmdd, today_str


class BirthdayService:
    def __init__(self, dao):
        self._dao = dao

    async def get_birthday_users(self, group_id: str) -> list:
        mmdd = today_mmdd()
        return await self._dao.get_birthday_users(group_id, mmdd)

    async def announce_birthdays(self, group_id: str) -> dict:
        """获取今日寿星列表（不标记已播报，标记由发送成功后由调用方完成）。"""
        today = today_str()
        already = await self._dao.was_birthday_announced(group_id, today)
        if already:
            return {"announced": False, "reason": "already_done"}

        users = await self.get_birthday_users(group_id)
        if not users:
            return {"announced": False, "reason": "no_birthdays"}

        return {"announced": True, "users": [u["qq"] for u in users]}
