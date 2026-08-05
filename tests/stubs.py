"""astrbot.api 桩：测试环境无 AstrBot 运行时，此处替换 logger/event/star/platform 依赖。

安全说明：本模块仅存在于 tests/ 目录，不随插件运行加载。
"""

import enum
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


class _MessageChain:
    """链式消息构造桩：message()/at() 返回自身，str() 按序拼接文本与 @ 目标。"""

    def __init__(self):
        self._parts = []

    def message(self, text):
        self._parts.append(("text", str(text)))
        return self

    def at(self, qq, name=None):
        self._parts.append(("at", str(qq)))
        return self

    def __str__(self):
        return "".join(p[1] for p in self._parts)

    def __repr__(self):
        return f"_MessageChain({self._parts})"


class _Plain:
    """astrbot.api.message_components.Plain 桩（type/text 属性对齐真实组件）。"""

    def __init__(self, text, convert=True, **_):
        self.type = "Plain"
        self.text = str(text)


class _At:
    """astrbot.api.message_components.At 桩（type/qq/name 属性对齐真实组件）。"""

    def __init__(self, qq, name="", **_):
        self.type = "At"
        self.qq = qq
        self.name = name


class _AtAll(_At):
    """astrbot.api.message_components.AtAll 桩（At 子类，qq="all"）。"""

    def __init__(self, **kwargs):
        super().__init__(qq="all", name=kwargs.get("name", "全体成员"))


class _Star:
    def __init__(self, context=None):
        self.context = context


def _passthrough(*a, **k):
    def deco(fn):
        return fn

    return deco


class _MessageType(enum.Enum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"


def install_stubs():
    if "astrbot" in sys.modules:
        return
    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    api_pkg = types.ModuleType("astrbot.api")
    api_pkg.logger = _Logger()

    event_pkg = types.ModuleType("astrbot.api.event")
    event_pkg.MessageEventResult = types.SimpleNamespace
    event_pkg.MessageChain = _MessageChain
    event_pkg.AstrMessageEvent = object

    filter_mod = types.ModuleType("astrbot.api.event.filter")
    filter_mod.regex = _passthrough
    filter_mod.command = _passthrough
    filter_mod.event_message_type = _passthrough
    filter_mod.EventMessageType = types.SimpleNamespace(GROUP_MESSAGE="group_message")
    event_pkg.filter = filter_mod
    sys.modules["astrbot.api.event.filter"] = filter_mod

    star_pkg = types.ModuleType("astrbot.api.star")
    star_pkg.Context = object
    star_pkg.Star = _Star
    star_pkg.register = lambda *a, **k: lambda cls: cls
    sys.modules["astrbot.api.star"] = star_pkg

    platform_pkg = types.ModuleType("astrbot.api.platform")
    platform_pkg.MessageType = _MessageType
    sys.modules["astrbot.api.platform"] = platform_pkg

    mc_pkg = types.ModuleType("astrbot.api.message_components")
    mc_pkg.Plain = _Plain
    mc_pkg.At = _At
    mc_pkg.AtAll = _AtAll
    sys.modules["astrbot.api.message_components"] = mc_pkg

    sys.modules["astrbot"] = astrbot_pkg
    sys.modules["astrbot.api"] = api_pkg
    sys.modules["astrbot.api.event"] = event_pkg
