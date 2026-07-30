import random
from utils.helpers import is_date_in_range


class DateRewardService:
    def __init__(self, dao):
        self._dao = dao

    async def check(self, today_mmdd: str, message: str) -> int:
        rewards = await self._dao.get_active_date_rewards()
        total = 0
        for r in rewards:
            if not is_date_in_range(today_mmdd, r["start_date"], r["end_date"]):
                continue
            kw = r["keyword"]
            if kw and kw.lower() not in message.lower():
                continue
            if random.random() <= r["probability"]:
                total += r["points"]
        return total
