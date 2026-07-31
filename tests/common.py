"""测试公共工具：临时库、FakeEvent、生成器收集、计时。"""
import asyncio
import os
import sys
import tempfile
import time
import types

from .stubs import install_stubs

install_stubs()

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.dirname(PLUGIN_ROOT)
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

from astrbot_plugin_point_system_by_whleague.db.connection import DatabaseManager
from astrbot_plugin_point_system_by_whleague.db.schema import init_schema
from astrbot_plugin_point_system_by_whleague.db.dao import PointDAO


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
    """最小 AstrMessageEvent 桩，支持 handler/service 层测试所需接口。"""

    def __init__(self, qq, group_id=None, is_admin=False, msg=""):
        self._qq = qq
        self._gid = group_id
        self._admin = is_admin
        self._msg = msg
        self.is_at_or_wake_command = False
        self.results = []
        self.bot = object()
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

    async def send(self, *a, **k):
        pass


async def collect(agen):
    """消费 async generator 并返回文本结果列表。"""
    out = []
    async for r in agen:
        out.append(getattr(r, "text", str(r)))
    return out


class Timer:
    def __init__(self):
        self.t0 = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0


def fmt_sec(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms" if seconds < 1 else f"{seconds:.2f}s"
