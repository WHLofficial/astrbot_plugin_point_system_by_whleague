"""astrbot.api 桩：测试环境无 AstrBot 运行时，此处替换 logger/event 依赖。

安全说明：本模块仅存在于 tests/ 目录，不随插件运行加载。
"""
import sys
import types


class _Logger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def install_stubs():
    if "astrbot" in sys.modules:
        return
    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    api_pkg = types.ModuleType("astrbot.api")
    api_pkg.logger = _Logger()
    event_pkg = types.ModuleType("astrbot.api.event")
    event_pkg.MessageEventResult = types.SimpleNamespace
    event_pkg.MessageChain = types.SimpleNamespace
    sys.modules["astrbot"] = astrbot_pkg
    sys.modules["astrbot.api"] = api_pkg
    sys.modules["astrbot.api.event"] = event_pkg
