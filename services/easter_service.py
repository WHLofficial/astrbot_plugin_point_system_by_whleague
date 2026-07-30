import random
from astrbot.api import logger


class EasterService:
    def __init__(self, dao):
        self._dao = dao

    async def trigger(self, qq: str, group_id: str, lucky_pity: int, unlucky_pity: int):
        events = await self._dao.get_active_easter_events()
        lucky_events = [e for e in events if e["event_type"] == "lucky"]
        unlucky_events = [e for e in events if e["event_type"] == "unlucky"]

        new_lucky_pity = lucky_pity + 1
        new_unlucky_pity = unlucky_pity + 1
        max_lucky_pity = max((e["pity_count"] for e in lucky_events), default=0)
        max_unlucky_pity = max((e["pity_count"] for e in unlucky_events), default=0)

        force_lucky = max_lucky_pity > 0 and new_lucky_pity >= max_lucky_pity
        force_unlucky = max_unlucky_pity > 0 and new_unlucky_pity >= max_unlucky_pity

        selected = None
        reset_lucky = False
        reset_unlucky = False

        if force_lucky:
            selected = random.choice(lucky_events) if lucky_events else None
            reset_lucky = True
        elif force_unlucky:
            selected = random.choice(unlucky_events) if unlucky_events else None
            reset_unlucky = True
        else:
            lucky_prob = sum(e["probability"] for e in lucky_events)
            unlucky_prob = sum(e["probability"] for e in unlucky_events)

            if lucky_events and random.random() < lucky_prob:
                selected = random.choices(
                    lucky_events, weights=[e["probability"] for e in lucky_events]
                )[0]
                reset_lucky = True
            elif unlucky_events and random.random() < unlucky_prob:
                selected = random.choices(
                    unlucky_events, weights=[e["probability"] for e in unlucky_events]
                )[0]
                reset_unlucky = True

        if reset_lucky:
            new_lucky_pity = 0
        if reset_unlucky:
            new_unlucky_pity = 0

        await self._dao.update_pity(qq, group_id, new_lucky_pity, new_unlucky_pity)

        if selected is None:
            return None

        points = random.randint(selected["points_min"], selected["points_max"])
        logger.info(
            f"Easter event '{selected['name']}' for {qq}@{group_id}: {points} points"
        )
        return {
            "event_type": selected["event_type"],
            "name": selected["name"],
            "points": points,
        }
