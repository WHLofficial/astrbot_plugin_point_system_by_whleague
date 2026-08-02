"""S17 指令图（Command map）：目录完整性 / 数据构建 / 签名 / 缓存 / 三级降级 / 限流。"""

import json
import os
import re
import tempfile
import time
import types
from unittest import mock

from .common import FakeEvent, base_cfg
from .stubs import install_stubs

install_stubs()

from astrbot_plugin_point_system_by_whleague.config.defaults import validate_and_cast
from astrbot_plugin_point_system_by_whleague.handlers.command_map import (
    CommandMapHandler,
)
from astrbot_plugin_point_system_by_whleague.services import command_map as cm
from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter


def _decode_escapes(s: str) -> str:
    """Decode Python source \\uXXXX escapes into real characters."""
    return bytes(s, "utf-8").decode("unicode_escape")


async def _collect_map(agen):
    out = []
    async for r in agen:
        out.append(getattr(r, "image", None) or getattr(r, "text", ""))
    return out


class _MapEvent(FakeEvent):
    def image_result(self, url_or_path):
        r = types.SimpleNamespace(image=url_or_path, text=None)
        self.results.append(r)
        return r


class _FakePlugin:
    def __init__(self, cfg, workdir):
        self.config_cache = cfg
        self.rate_limiter = RateLimiter()
        self.workdir = workdir
        self.html_calls = 0
        self.t2i_calls = 0
        self.fail_html = False
        self.fail_t2i = False
        self.html_url = False

    async def html_render(self, *a, **k):
        self.html_calls += 1
        if self.fail_html:
            raise RuntimeError("endpoint down")
        if self.html_url:
            return "https://t2i.example/generated/poster.png"
        p = os.path.join(self.workdir, "poster.png")
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nposter")
        return p

    async def text_to_image(self, text, return_url=False):
        self.t2i_calls += 1
        if self.fail_t2i:
            raise RuntimeError("t2i down")
        p = os.path.join(self.workdir, "md.png")
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nmd")
        return p


async def test_catalog_integrity():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "main.py"), encoding="utf-8") as f:
        main_src = f.read()
    cmd_re = re.compile(
        r'@filter\.command\("([^"]+)"(?:,\s*alias=\{(.*?)\})?\)'
    )
    registered = set()
    aliases = set()
    for m in cmd_re.finditer(main_src):
        registered.add(_decode_escapes(m.group(1)))
        if m.group(2):
            for a in re.findall(r'"([^"]+)"', m.group(2)):
                aliases.add(_decode_escapes(a))
    assert registered, "no @filter.command found in main.py"

    entries = [e for s in cm._COMMAND_SECTIONS for e in s["entries"]]
    for name in sorted(registered):
        assert any(
            e["name"] == f"/{name}" or e["name"].startswith(f"/{name} ")
            for e in entries
        ), f"catalog missing command: {name}"
    # 无前缀关键词区块存在且全部为 keyword 触发
    kw_entries = [
        e
        for s in cm._COMMAND_SECTIONS
        if s["id"] == "member_noprefix"
        for e in s["entries"]
    ]
    assert kw_entries and all(e["trigger"] == "keyword" for e in kw_entries)
    return f"目录完整性：{len(registered)} 指令 + {len(aliases)} 别名全部登记"


async def test_build_data_dynamic():
    cfg = base_cfg(
        keyword_sign=["签到", "打卡"],
        keyword_lottery=["抽奖"],
        lottery_passphrase="whl2026",
    )
    data = cm.build_map_data(cfg)
    text = json.dumps(data, ensure_ascii=False)
    assert "签到 / 打卡" in text and "whl2026" in text
    assert "{sign_kw}" not in text
    assert "{lottery_kw}" not in text
    assert "{passphrase}" not in text
    assert data["version"] == cm._cfg_defaults.PLUGIN_VERSION
    assert {s["id"] for s in data["sections"]} == {
        "member_noprefix",
        "member_cmd",
        "admin",
    }
    return "动态注入：关键词/口令替换、无占位符残留、区块齐全"


async def test_markdown_and_poster_data():
    cfg = base_cfg(keyword_sign=["<b>打卡</b>"], lottery_passphrase="a|b")
    data = cm.build_map_data(cfg)
    md = cm.build_markdown(data)
    assert "# 积分系统指令总览" in md
    assert "管理员指令" in md and "成员指令" in md
    assert "a\\|b" in md  # 表格单元格转义
    assert "{" not in md  # 无占位符残留
    pd = cm.poster_data(data)
    assert "&lt;b&gt;打卡&lt;/b&gt;" in json.dumps(pd, ensure_ascii=False)
    return "Markdown/海报数据：区块、转义、无占位符残留"


async def test_signature_stability_and_invalidation():
    s1 = cm.cache_signature(cm.build_map_data(base_cfg()))
    assert len(s1) == 16
    assert s1 == cm.cache_signature(cm.build_map_data(base_cfg()))
    # 配置变化强制换键
    s2 = cm.cache_signature(cm.build_map_data(base_cfg(lottery_passphrase="x")))
    assert s2 != s1
    # 版本变化强制换键（升级后下次触发即重渲染）
    with mock.patch.object(cm._cfg_defaults, "PLUGIN_VERSION", "9.9.9"):
        s3 = cm.cache_signature(cm.build_map_data(base_cfg()))
        assert s3 != s1
    return "签名：同配置稳定 / 配置与版本变化强制换键"


async def test_cache_store_get_sweep_oversize():
    with tempfile.TemporaryDirectory() as td:
        cache = cm.CommandMapCache(td)
        src = os.path.join(td, "render.png")
        with open(src, "wb") as f:
            f.write(b"imgdata")
        path = cache.store("abc123", src)
        assert path == os.path.join(td, "abc123.png")
        assert cache.get("abc123") == path
        os.unlink(src)  # 缓存命中不依赖源文件
        assert cache.get("abc123") == path
        assert cache.get("nope") is None
        os.unlink(path)  # 缓存文件丢失后失效
        assert cache.get("abc123") is None
        # 不存在的源文件拒绝
        assert cache.store("x", os.path.join(td, "missing.png")) is None
        # 超限文件直接透传不缓存
        big = os.path.join(td, "big.png")
        with open(big, "wb") as f:
            f.write(b"x" * (cm._MAX_IMAGE_BYTES + 1))
        out = cache.store("big", big)
        assert out == big and cache.get("big") is None
        # sweep 仅清理过期文件
        old_path = os.path.join(td, "old.png")
        with open(old_path, "wb") as f:
            f.write(b"old")
        os.utime(old_path, (time.time() - cm._CACHE_TTL - 60,) * 2)
        fresh = os.path.join(td, "fresh.png")
        with open(fresh, "wb") as f:
            f.write(b"fresh")
        cache.sweep()
        assert not os.path.exists(old_path)
        assert os.path.exists(fresh)
    return "缓存：存取/失效/超限透传/过期清扫"


async def test_cache_ttl_hot_expiry_and_disable():
    with tempfile.TemporaryDirectory() as td:
        cache = cm.CommandMapCache(td)
        src = os.path.join(td, "render.png")
        with open(src, "wb") as f:
            f.write(b"imgdata")
        # 短 TTL 存储后热失效
        path = cache.store("hot", src, ttl_seconds=3600)
        assert cache.get("hot", ttl_seconds=3600) == path
        old = time.time() - 3601
        os.utime(path, (old, old))
        assert cache.get("hot", ttl_seconds=3600) is None
        assert not os.path.exists(path)  # 过期条目同时被清理
        # ttl=0：get 恒 miss
        path2 = cache.store("zero", src, ttl_seconds=0)
        assert path2 == src  # 直接透传不落盘
        assert cache.get("zero", ttl_seconds=0) is None
        assert not os.path.exists(os.path.join(td, "zero.png"))
        # 已缓存条目在 ttl=0 时也不可读
        path3 = cache.store("dis", src, ttl_seconds=3600)
        assert cache.get("dis", ttl_seconds=0) is None
        assert os.path.exists(path3)
    return "缓存：TTL 热失效 / 0=禁用缓存"


async def test_handler_render_and_cache_hit():
    with tempfile.TemporaryDirectory() as td:
        plugin = _FakePlugin(base_cfg(), td)
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        cached = os.path.join(
            td, cm.cache_signature(cm.build_map_data(base_cfg())) + ".png"
        )
        out = await _collect_map(handler.handle(_MapEvent("u1", "G1", msg="/积分系统帮助")))
        assert len(out) == 1 and out[0] == cached
        assert plugin.html_calls == 1 and plugin.t2i_calls == 0
        # 不同群/用户二次触发命中缓存，零渲染
        out2 = await _collect_map(handler.handle(_MapEvent("u2", "G2", msg="/指令图")))
        assert out2 == [cached]
        assert plugin.html_calls == 1 and plugin.t2i_calls == 0
    return "handler：海报渲染 + 缓存命中零开销"


async def test_handler_rate_limited():
    with tempfile.TemporaryDirectory() as td:
        plugin = _FakePlugin(base_cfg(), td)
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        out = await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        assert out and "过于频繁" not in out[0]
        # 同用户同群立即重试被限流
        out2 = await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        assert out2 and "过于频繁" in out2[0]
        # 其他用户其他群不受影响
        out3 = await _collect_map(handler.handle(_MapEvent("u2", "G2")))
        assert out3 and "过于频繁" not in out3[0]
    return "限流：用户冷却生效、不同用户/群不受影响"


async def test_handler_cooldown_from_config():
    with tempfile.TemporaryDirectory() as td:
        # 冷却 0 = 不限流：同用户同群连续触发不被拦截
        plugin = _FakePlugin(base_cfg(cmd_map_user_cooldown=0, cmd_map_group_cooldown=0), td)
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        out = await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        assert out and "过于频繁" not in out[0]
        out2 = await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        assert out2 and "过于频繁" not in out2[0]
    return "限流：冷却值从配置读取，0=不限流"


async def test_handler_ttl_zero_disables_cache():
    with tempfile.TemporaryDirectory() as td:
        plugin = _FakePlugin(base_cfg(cmd_map_cache_ttl_hours=0), td)
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        sig = cm.cache_signature(cm.build_map_data(base_cfg()))
        await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        await _collect_map(handler.handle(_MapEvent("u2", "G2")))
        # 每次触发都重新渲染，且不落盘缓存
        assert plugin.html_calls == 2
        assert not os.path.exists(os.path.join(td, sig + ".png"))
    return "配置：TTL=0 每次强制重渲染且不写缓存"


async def test_config_validate():
    assert validate_and_cast("cmd_map_user_cooldown", "15") == 15
    assert validate_and_cast("cmd_map_group_cooldown", "0") == 0
    assert validate_and_cast("cmd_map_cache_ttl_hours", "48") == 48
    for key in ("cmd_map_user_cooldown", "cmd_map_group_cooldown", "cmd_map_cache_ttl_hours"):
        try:
            validate_and_cast(key, "-5")
            raise AssertionError(f"negative accepted for {key}")
        except ValueError:
            pass
        try:
            validate_and_cast(key, "abc")
            raise AssertionError(f"non-numeric accepted for {key}")
        except ValueError:
            pass
    return "配置：三个新键 /设置 校验（数值/0/负值拒绝）"


async def test_handler_fallback_markdown():
    with tempfile.TemporaryDirectory() as td:
        plugin = _FakePlugin(base_cfg(), td)
        plugin.fail_html = True
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        cached = os.path.join(
            td, cm.cache_signature(cm.build_map_data(base_cfg())) + ".png"
        )
        out = await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        assert out == [cached]
        assert plugin.html_calls == 1 and plugin.t2i_calls == 1
    return "降级：海报失败自动回退 Markdown 文转图"


async def test_handler_http_result_treated_as_failure():
    with tempfile.TemporaryDirectory() as td:
        plugin = _FakePlugin(base_cfg(), td)
        plugin.html_url = True  # 端点异常返回 URL 而非本地路径
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        cached = os.path.join(
            td, cm.cache_signature(cm.build_map_data(base_cfg())) + ".png"
        )
        out = await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        assert out == [cached]
        assert plugin.t2i_calls == 1
    return "降级：海报返回 URL 视为失败并回退"


async def test_handler_fallback_plain_text():
    with tempfile.TemporaryDirectory() as td:
        plugin = _FakePlugin(base_cfg(), td)
        plugin.fail_html = plugin.fail_t2i = True
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        out = await _collect_map(handler.handle(_MapEvent("u1", "G1")))
        assert out and "# 积分系统指令总览" in out[0]
        # 渲染失败不写缓存
        assert os.listdir(td) == []
    return "降级：全失败回退纯文本且不污染缓存"


async def test_handler_private_chat():
    with tempfile.TemporaryDirectory() as td:
        plugin = _FakePlugin(base_cfg(), td)
        handler = CommandMapHandler(plugin, cache=cm.CommandMapCache(td))
        cached = os.path.join(
            td, cm.cache_signature(cm.build_map_data(base_cfg())) + ".png"
        )
        out = await _collect_map(handler.handle(_MapEvent("u1", None, msg="/帮助图")))
        assert out == [cached]
    return "私聊：无群场景可正常生成"


TESTS = [
    ("catalog_integrity", test_catalog_integrity),
    ("build_data_dynamic", test_build_data_dynamic),
    ("markdown_poster_data", test_markdown_and_poster_data),
    ("signature", test_signature_stability_and_invalidation),
    ("cache", test_cache_store_get_sweep_oversize),
    ("cache_ttl", test_cache_ttl_hot_expiry_and_disable),
    ("handler_render_cache", test_handler_render_and_cache_hit),
    ("handler_rate_limit", test_handler_rate_limited),
    ("handler_cooldown_cfg", test_handler_cooldown_from_config),
    ("handler_ttl_zero", test_handler_ttl_zero_disables_cache),
    ("config_validate", test_config_validate),
    ("handler_fallback_markdown", test_handler_fallback_markdown),
    ("handler_http_fallback", test_handler_http_result_treated_as_failure),
    ("handler_fallback_text", test_handler_fallback_plain_text),
    ("handler_private", test_handler_private_chat),
]
