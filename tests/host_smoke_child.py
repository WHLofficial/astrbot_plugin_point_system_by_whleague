"""真实 AstrBot 宿主加载子进程（由 tests/s21_host_compat.py 调用，不单独运行）。

在真实 astrbot 环境下验证插件可被宿主加载：Star 子类自动注册、handler 进入
注册表、metadata 版本门禁通过、initialize/terminate 生命周期可完整跑通。

隔离措施：必须通过 ASTRBOT_ROOT 指向临时目录（由父进程设置），确保插件数据
目录落在临时路径，绝不触碰生产 data/plugin_data/points_system.db。

退出码：0=通过；3=环境不具备（无法导入 astrbot 或缺核心依赖），父进程按跳过处理；
其余=失败（stderr 含原因）。
"""

import asyncio
import os
import sys

CORE_ROOT = os.environ.get("ASTRBOT_CORE", "")
PLUGIN_PARENT = os.environ.get("PLUGIN_PARENT", "")

if not CORE_ROOT or not PLUGIN_PARENT:
    print("SKIP: ASTRBOT_CORE/PLUGIN_PARENT not set", file=sys.stderr)
    sys.exit(3)

# 安全红线：本脚本会真实执行插件 initialize() 并创建数据库。必须由父测试进程
# 将 ASTRBOT_ROOT 指向临时目录；缺省时直接拒绝运行，避免误写生产 plugin_data。
if not os.environ.get("ASTRBOT_ROOT"):
    print("REFUSE: ASTRBOT_ROOT not set; refusing to run against production data", file=sys.stderr)
    sys.exit(3)

# 真实核心路径优先于任何已安装包
sys.path.insert(0, CORE_ROOT)
sys.path.insert(0, PLUGIN_PARENT)

try:
    import astrbot
except Exception as e:  # noqa: BLE001
    print(f"SKIP: cannot import astrbot: {e}", file=sys.stderr)
    sys.exit(3)

try:
    from astrbot.api.star import Star
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import star_handlers_registry
except Exception as e:  # noqa: BLE001
    print(f"SKIP: astrbot core deps unavailable: {e}", file=sys.stderr)
    sys.exit(3)

try:
    import aiosqlite  # noqa: F401
except Exception as e:  # noqa: BLE001
    print(f"SKIP: aiosqlite unavailable: {e}", file=sys.stderr)
    sys.exit(3)

print(f"astrbot {astrbot.__version__}")

from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin  # noqa: E402

MODULE = PointSystemPlugin.__module__

assert issubclass(PointSystemPlugin, Star), "plugin must subclass Star"


def _check_autoregister() -> None:
    """Star 子类应自动注册（不依赖已废弃的 @register）。"""
    assert MODULE in star_map, f"plugin module {MODULE} not in star_map (auto-register failed)"
    handlers = star_handlers_registry.get_handlers_by_module_name(MODULE)
    assert handlers, "no handlers registered for the plugin"
    print(f"auto-register OK: {len(handlers)} handlers")


def _check_version_gate() -> None:
    """metadata.yaml 的 astrbot_version 必须覆盖当前宿主版本。"""
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
    except ImportError:
        print("version gate: packaging unavailable, skipped")
        return

    import pathlib
    import re

    meta_path = pathlib.Path(PLUGIN_PARENT) / "astrbot_plugin_point_system_by_whleague" / "metadata.yaml"
    meta = meta_path.read_text(encoding="utf-8")
    m = re.search(r'astrbot_version:\s*"?([^"\n]+)"?', meta)
    assert m, "metadata.yaml missing astrbot_version"
    spec = m.group(1).strip()
    assert Version(astrbot.__version__) in SpecifierSet(spec), (
        f"host {astrbot.__version__} does not satisfy {spec}"
    )
    print(f"version gate OK: {spec} covers {astrbot.__version__}")


class _Cron:
    """最小 cron 管理器替身（真实实现需完整核心生命周期，此处只验证调用契约）。"""

    def __init__(self) -> None:
        self.jobs: list = []

    async def add_basic_job(self, **kwargs):
        job = type("J", (), {})()
        job.job_id = f"smoke-{len(self.jobs)}"
        job.persistent = False
        job.job_type = "basic"
        job.name = kwargs.get("name")
        self.jobs.append(job)
        return job

    async def delete_job(self, job_id):
        self.jobs = [j for j in self.jobs if j.job_id != job_id]

    async def list_jobs(self, job_type=None):
        return list(self.jobs)


class _PlatformManager:
    def __init__(self) -> None:
        self.platform_insts: list = []


class _Context:
    def __init__(self) -> None:
        self.cron_manager = _Cron()
        self.platform_manager = _PlatformManager()


async def _lifecycle() -> None:
    plugin = PointSystemPlugin(_Context(), config=None)
    await plugin.initialize()
    await plugin.terminate()


_check_autoregister()
_check_version_gate()
asyncio.run(_lifecycle())
print("lifecycle OK: initialize/terminate completed")
print("REAL HOST LOAD OK")
