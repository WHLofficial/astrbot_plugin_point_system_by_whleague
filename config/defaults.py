import json

DEFAULT_CONFIG = {
    "signin_fixed_mode": False,
    "signin_fixed_points": 10,
    "signin_random_min": 1,
    "signin_random_max": 20,
    "signin_first_bonus": 50,
    "signin_day_first_bonus": 30,
    "signin_consecutive_max": 30,
    "signin_consecutive_bonus_per_day": 5,
    "signin_weekly_bonus": 100,
    "active_reward_enabled": True,
    "active_reward_probability": 0.05,
    "active_reward_points": 1,
    "active_reward_cooldown": 60,
    "active_reward_min_length": 3,
    "active_reward_global_cooldown": 10,
    "lottery_enabled": True,
    "lottery_cost": 100,
    "lottery_passphrase": "whl",
    "lottery_tiers": json.dumps({
        "tiers": [
            {"label": "特等奖", "weight": 1, "multiplier": 10.0, "emoji": "\U0001f451"},
            {"label": "一等奖", "weight": 5, "multiplier": 5.0, "emoji": "\U0001f947"},
            {"label": "二等奖", "weight": 15, "multiplier": 2.0, "emoji": "\U0001f948"},
            {"label": "三等奖", "weight": 30, "multiplier": 1.2, "emoji": "\U0001f949"},
            {"label": "参与奖", "weight": 49, "multiplier": 0.0, "emoji": "\u2728"},
        ]
    }),
    "negative_disable_lottery": True,
    "birthday_bonus_points": 100,
    "birthday_announce_time": "08:00",
    "backup_enabled": True,
    "keyword_sign": json.dumps(["\u7b7e\u5230", "sign", "\u6253\u5361"]),
    "keyword_lottery": json.dumps(["\u62bd\u5956", "lottery"]),
}

TYPE_MAP = {
    "signin_fixed_mode": bool,
    "signin_fixed_points": int,
    "signin_random_min": int,
    "signin_random_max": int,
    "signin_first_bonus": int,
    "signin_day_first_bonus": int,
    "signin_consecutive_max": int,
    "signin_consecutive_bonus_per_day": int,
    "signin_weekly_bonus": int,
    "active_reward_enabled": bool,
    "active_reward_probability": float,
    "active_reward_points": int,
    "active_reward_cooldown": int,
    "active_reward_min_length": int,
    "active_reward_global_cooldown": int,
    "lottery_enabled": bool,
    "lottery_cost": int,
    "lottery_passphrase": str,
    "lottery_tiers": str,
    "negative_disable_lottery": bool,
    "birthday_bonus_points": int,
    "birthday_announce_time": str,
    "backup_enabled": bool,
    "keyword_sign": str,
    "keyword_lottery": str,
}


def get_default(key: str):
    return DEFAULT_CONFIG.get(key)


def cast_value(key: str, raw: str):
    t = TYPE_MAP.get(key, str)
    if t == bool:
        return raw.lower() in ("true", "1", "yes")
    if t == int:
        return int(raw)
    if t == float:
        return float(raw)
    return raw
