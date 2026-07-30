import time
from collections import defaultdict


class RateLimiter:
    def __init__(self):
        self._user_cooldowns: dict[str, float] = {}
        self._group_cooldowns: dict[str, float] = {}

    def _user_key(self, action: str, qq: str, group_id: str) -> str:
        return f"{action}:{qq}:{group_id}"

    def check_user(self, action: str, qq: str, group_id: str, cooldown: int) -> bool:
        if cooldown <= 0:
            return True
        key = self._user_key(action, qq, group_id)
        now = time.time()
        last = self._user_cooldowns.get(key, 0)
        if now - last < cooldown:
            return False
        self._user_cooldowns[key] = now
        return True

    def check_group(self, action: str, group_id: str, cooldown: int) -> bool:
        if cooldown <= 0:
            return True
        key = f"{action}:{group_id}"
        now = time.time()
        last = self._group_cooldowns.get(key, 0)
        if now - last < cooldown:
            return False
        self._group_cooldowns[key] = now
        return True

    def clear(self):
        self._user_cooldowns.clear()
        self._group_cooldowns.clear()
