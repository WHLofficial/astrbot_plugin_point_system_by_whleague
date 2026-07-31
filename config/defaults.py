import json
import re

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
    "lottery_daily_limit": 10,
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
    "signin_refresh_time": "04:00",
    "backup_enabled": True,
    "backup_time": "04:00",
    "backup_dirs": json.dumps([]),
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
    "lottery_daily_limit": int,
    "lottery_passphrase": str,
    "lottery_tiers": str,
    "negative_disable_lottery": bool,
    "birthday_bonus_points": int,
    "birthday_announce_time": str,
    "signin_refresh_time": str,
    "backup_enabled": bool,
    "backup_time": str,
    "backup_dirs": str,
    "keyword_sign": str,
    "keyword_lottery": str,
}

_KEYWORD_KEYS = ("keyword_sign", "keyword_lottery")
_LIST_KEYS = _KEYWORD_KEYS + ("backup_dirs",)


def parse_keyword_list(raw):
    """将配置中的关键词解析为列表，兼容 JSON 数组或逗号分隔文本。"""
    if isinstance(raw, (list, tuple)):
        return [str(k) for k in raw if k]
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    if not s:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(k) for k in data if k]
    except json.JSONDecodeError:
        pass
    return [k.strip() for k in s.split(",") if k.strip()]


def validate_and_cast(key: str, raw: str):
    """校验并转换管理员通过 /设置 传入的配置值。

    Args:
        key: 配置项名称。
        raw: 原始字符串值。

    Returns:
        转换后的缓存值（关键词类配置返回 list）。

    Raises:
        ValueError: 配置项不存在或值非法。
    """
    if key not in DEFAULT_CONFIG:
        raise ValueError(f"\u672a\u77e5\u914d\u7f6e\u9879: {key}")

    if key in _LIST_KEYS:
        lst = parse_keyword_list(raw)
        if key in _KEYWORD_KEYS and not lst:
            raise ValueError(f"\u914d\u7f6e {key} \u4e0d\u80fd\u4e3a\u7a7a")
        return lst

    if key in ("birthday_announce_time", "signin_refresh_time", "backup_time"):
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", raw.strip()):
            raise ValueError(f"{key} \u9700\u4e3a HH:MM \u683c\u5f0f")
        return raw.strip()

    if key == "lottery_passphrase":
        s = raw.strip()
        if not s:
            raise ValueError("lottery_passphrase 不能为空")
        return s

    if key == "lottery_tiers":
        try:
            data = json.loads(raw)
            if not (
                isinstance(data, dict)
                and isinstance(data.get("tiers"), list)
                and data["tiers"]
            ):
                raise ValueError
            for t in data["tiers"]:
                if not isinstance(t, dict):
                    raise ValueError
                label = t.get("label")
                if not isinstance(label, str) or not label.strip():
                    raise ValueError
                weight = t.get("weight")
                multiplier = t.get("multiplier")
                if not isinstance(weight, (int, float)) or weight <= 0:
                    raise ValueError
                if not isinstance(multiplier, (int, float)) or multiplier < 0:
                    raise ValueError
                if multiplier > 100:
                    raise ValueError
        except (json.JSONDecodeError, ValueError):
            raise ValueError('lottery_tiers \u9700\u4e3a\u5408\u6cd5 JSON ({"tiers": [{"label": "...", "weight": >0, "multiplier": 0~100, ...}]})')
        return raw

    t = TYPE_MAP.get(key, str)
    if t == bool:
        low = raw.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        raise ValueError(f"\u914d\u7f6e {key} \u9700\u4e3a\u5e03\u5c14\u503c (true/false/1/0)")
    if t == int:
        try:
            parsed = int(raw.strip())
        except ValueError:
            raise ValueError(f"\u914d\u7f6e {key} \u9700\u4e3a\u6574\u6570")
        if parsed < 0:
            raise ValueError(f"\u914d\u7f6e {key} \u4e0d\u80fd\u4e3a\u8d1f\u6570")
        return parsed
    if t == float:
        try:
            parsed = float(raw.strip())
        except ValueError:
            raise ValueError(f"\u914d\u7f6e {key} \u9700\u4e3a\u6570\u5b57")
        if key == "active_reward_probability" and not (0.0 <= parsed <= 1.0):
            raise ValueError("active_reward_probability \u9700\u5728 0~1 \u4e4b\u95f4")
        return parsed
    return raw
