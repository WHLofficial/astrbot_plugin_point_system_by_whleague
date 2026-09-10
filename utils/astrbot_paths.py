"""AstrBot 宿主路径解析：集中封装私有 core 路径 API，隔离核心重构风险。

插件只通过本模块获取数据目录，避免在多处直接 import
``astrbot.core.utils.astrbot_path``（非公开 API，核心可能移动该模块）。
解析顺序：插件数据目录 → AstrBot 数据目录 → ``ASTRBOT_ROOT`` → 当前工作目录，
保证在 AstrBot 内运行时落于 ``data/plugin_data``，同时兼容独立运行与测试。
"""

import os

PLUGIN_NAME = "astrbot_plugin_point_system_by_whleague"


def _resolve_base_dir() -> str:
    """返回插件数据目录的父级 base 路径。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        return get_astrbot_plugin_data_path()
    except Exception:
        pass
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return get_astrbot_data_path()
    except Exception:
        pass
    root = os.environ.get("ASTRBOT_ROOT")
    if root:
        return os.path.realpath(os.path.join(root, "data"))
    return os.getcwd()


def get_plugin_data_dir() -> str:
    """返回本插件专属数据目录，并确保其存在。"""
    path = os.path.join(_resolve_base_dir(), PLUGIN_NAME)
    os.makedirs(path, exist_ok=True)
    return path
