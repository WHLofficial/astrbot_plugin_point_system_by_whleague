import json
import math
import os
import re

PLUGIN_VERSION = "0.2.1"
"""插件版本号。

需要与 metadata.yaml 的 version 保持一致（发布时同步更新）。
参与指令图缓存签名：版本变化时自动强制重新渲染指令图。
"""

_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json"
)

_TYPE_DEFAULTS = {
    "int": 0,
    "float": 0.0,
    "bool": False,
    "string": "",
    "text": "",
    "list": [],
    "file": [],
    "object": {},
    "template_list": [],
    "dict": {},
}

_TYPE_MAP = {
    "int": int,
    "float": float,
    "bool": bool,
    "string": str,
    "text": str,
    "list": str,
}


def _load_schema() -> dict:
    if not os.path.exists(_SCHEMA_PATH):
        raise RuntimeError(f"缺少插件配置 schema 文件: {_SCHEMA_PATH}")
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    for key, meta in schema.items():
        if meta.get("type") not in _TYPE_DEFAULTS:
            raise RuntimeError(f"配置项 {key} 的类型 {meta.get('type')} 不受支持")
    return schema


_SCHEMA = _load_schema()

# 默认值唯一来源为 _conf_schema.json（与 AstrBot WebUI 展示/校验一致）
DEFAULT_CONFIG = {
    key: meta.get("default", _TYPE_DEFAULTS[meta["type"]])
    for key, meta in _SCHEMA.items()
}

TYPE_MAP = {key: _TYPE_MAP[meta["type"]] for key, meta in _SCHEMA.items()}

_KEYWORD_KEYS = tuple(
    key
    for key, meta in _SCHEMA.items()
    if meta["type"] == "list" and key in ("keyword_sign", "keyword_lottery")
)
_LIST_KEYS = tuple(key for key, meta in _SCHEMA.items() if meta["type"] == "list")


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
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", raw.strip())
        if not m:
            raise ValueError(f"{key} \u9700\u4e3a HH:MM \u683c\u5f0f")
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"

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
                points_min = t.get("points_min")
                points_max = t.get("points_max")
                if (
                    not isinstance(weight, (int, float))
                    or not math.isfinite(weight)
                    or weight <= 0
                ):
                    raise ValueError
                if (
                    not isinstance(points_min, int)
                    or not isinstance(points_max, int)
                    or isinstance(points_min, bool)
                    or isinstance(points_max, bool)
                    or points_min < 0
                    or points_max < points_min
                ):
                    raise ValueError
        except (json.JSONDecodeError, ValueError):
            raise ValueError(
                'lottery_tiers \u9700\u4e3a\u5408\u6cd5 JSON ({"tiers": [{"label": "...", "weight": >0, "points_min": 0~points_max, "points_max": int, ...}]})'
            )
        return raw

    t = TYPE_MAP.get(key, str)
    if t is bool:
        low = raw.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        raise ValueError(
            f"\u914d\u7f6e {key} \u9700\u4e3a\u5e03\u5c14\u503c (true/false/1/0)"
        )
    if t is int:
        try:
            parsed = int(raw.strip())
        except ValueError:
            raise ValueError(f"\u914d\u7f6e {key} \u9700\u4e3a\u6574\u6570")
        if parsed < 0:
            raise ValueError(f"\u914d\u7f6e {key} \u4e0d\u80fd\u4e3a\u8d1f\u6570")
        return parsed
    if t is float:
        try:
            parsed = float(raw.strip())
        except ValueError:
            raise ValueError(f"\u914d\u7f6e {key} \u9700\u4e3a\u6570\u5b57")
        if key == "active_reward_probability" and not (0.0 <= parsed <= 1.0):
            raise ValueError("active_reward_probability \u9700\u5728 0~1 \u4e4b\u95f4")
        return parsed
    return raw
