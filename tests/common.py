"""测试公共工具：临时库、FakeEvent、FakeBot/FakeContext、生成器收集、确定性随机、计时。

说明：插件导入必须位于 install_stubs() 与 sys.path 调整之后，导入顺序为有意为之。
"""
# ruff: noqa: I001, E402

import os
import sys
import tempfile
import time
import types
from contextlib import contextmanager
from unittest import mock

from .stubs import install_stubs

install_stubs()

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.dirname(PLUGIN_ROOT)
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

from astrbot_plugin_point_system_by_whleague.config.defaults import (
    _LIST_KEYS,
    DEFAULT_CONFIG,
    parse_keyword_list,
)
from astrbot_plugin_point_system_by_whleague.db.connection import DatabaseManager
from astrbot_plugin_point_system_by_whleague.db.dao import PointDAO
from astrbot_plugin_point_system_by_whleague.db.schema import init_schema
from astrbot_plugin_point_system_by_whleague.utils import helpers as _helpers


class TempDB:
    """临时 SQLite 库，测试后自动关闭（绝不触碰生产库）。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="points_test_")
        self.path = os.path.join(self.dir, "test.db")

    async def __aenter__(self):
        self.db = DatabaseManager(self.path)
        await self.db.init()
        await init_schema(self.db)
        self.dao = PointDAO(self.db)
        return self

    async def __aexit__(self, *exc):
        try:
            await self.db.close()
        finally:
            return False

    async def count(self, table: str) -> int:
        row = await self.db.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
        return row["c"] if row else 0


class FakeEvent:
    """最小 AstrMessageEvent 桩，支持 handler/service 层测试所需接口。

    Args:
        qq: 发送者 QQ。
        group_id: 群 ID。
        is_admin: 是否为 AstrBot 全局管理员。
        msg: 消息文本。
        group_owner: 群主 QQ；为 None 时 get_group() 返回 None（非群主）。
        at_wake: 是否为 @bot / 唤醒前缀命令消息。
        bot: 平台 bot 桩（默认空对象）。
    """

    def __init__(
        self,
        qq,
        group_id=None,
        is_admin=False,
        msg="",
        group_owner=None,
        at_wake=False,
        bot=None,
    ):
        self._qq = qq
        self._gid = group_id
        self._admin = is_admin
        self._msg = msg
        self._group_owner = group_owner
        self.is_at_or_wake_command = at_wake
        self.results = []
        self.sent = []
        self.bot = bot if bot is not None else object()
        self._platform = "aiocqhttp"
        self._sender_name = f"昵称{qq}"

    def get_sender_id(self):
        return self._qq

    def get_group_id(self):
        return self._gid

    def get_message_str(self):
        return self._msg

    def get_platform_name(self):
        return self._platform

    def get_sender_name(self):
        return self._sender_name

    def is_admin(self):
        return self._admin

    def plain_result(self, text):
        r = types.SimpleNamespace(text=text)
        self.results.append(r)
        return r

    async def get_group(self):
        if self._group_owner is None:
            return None
        return types.SimpleNamespace(group_owner=self._group_owner)

    def stop_event(self):
        pass

    async def send(self, chain, *a, **k):
        self.sent.append(chain)


class FakeBot:
    """记录 call_action 调用的假平台 bot（验证群名片设置/成员信息获取）。"""

    def __init__(self, member_card=None):
        self.calls = []
        self.member_card = member_card

    async def call_action(self, action, **kwargs):
        self.calls.append((action, dict(kwargs)))
        if action == "get_group_member_info":
            return {"card": self.member_card, "user_id": kwargs.get("user_id")}
        return None


class FakeContext:
    """假 AstrBot Context：记录定时任务注册与群消息发送。"""

    def __init__(self):
        self.cron_jobs = []
        self.sent = []
        self.cron_manager = types.SimpleNamespace(add_basic_job=self._add_job)

    async def _add_job(self, name, cron_expression, handler, description):
        self.cron_jobs.append({"name": name, "cron": cron_expression})
        return types.SimpleNamespace(name=name, remove=lambda: None)

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))


async def collect(agen):
    """消费 async generator 并返回文本结果列表。"""
    out = []
    async for r in agen:
        out.append(getattr(r, "text", str(r)))
    return out


def base_cfg(**overrides) -> dict:
    """完整业务配置缓存：默认值与 _conf_schema.json 一致，列表键已解析为 list。

    Args:
        overrides: 覆盖默认值的键值对。

    Returns:
        新配置字典（不修改 DEFAULT_CONFIG）。
    """
    cfg = dict(DEFAULT_CONFIG)
    for key in _LIST_KEYS:
        cfg[key] = parse_keyword_list(cfg[key])
    cfg.update(overrides)
    return cfg


@contextmanager
def patch_random(**fixed):
    """上下文管理器：固定 random 模块随机函数的返回值。

    Args:
        fixed: 形如 random=0.5 / randint=42 / uniform=0.3 / choices="欧皇降临" 的键值，
               分别固定 random.random / random.randint / random.uniform / random.choices。

    Yields:
        None（退出时自动恢复所有补丁）。
    """
    patchers = []
    for name, value in fixed.items():
        if name == "choices":
            patcher = mock.patch("random.choices", return_value=[value])
        else:
            patcher = mock.patch(f"random.{name}", return_value=value)
        patchers.append(patcher)
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patchers):
            p.stop()


def snapshot_day_boundary() -> tuple:
    """快照当前业务日分界（避免测试污染全局状态）。"""
    return _helpers.get_day_boundary()


def restore_day_boundary(boundary) -> None:
    """恢复业务日分界到快照值。"""
    _helpers.set_day_boundary(f"{boundary[0]}:{boundary[1]:02d}")


class Timer:
    def __init__(self):
        self.t0 = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0


def fmt_sec(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms" if seconds < 1 else f"{seconds:.2f}s"
