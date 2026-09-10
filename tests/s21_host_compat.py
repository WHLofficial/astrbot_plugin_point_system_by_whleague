"""S21 AstrBot 4.28.0 适配守卫：静态符号面校验 + 真实宿主加载冒烟。

背景（见 PLUGIN_PROMPT/适配核验）：插件与 AstrBot 4.28.0 API 完全兼容，本套件
不重复验证业务逻辑，而是把"兼容性契约"固化为回归测试，防止后续改动悄悄引入
宿主不支持或已废弃的用法：

1. 静态面（本机即可运行，无需核心依赖）：
   - metadata.yaml 版本门禁覆盖 4.28.0 且封顶 <5；
   - 不再使用已废弃的 @register，改由 Star 子类自动注册；
   - 不再直接 import 私有模块 astrbot.core.utils.astrbot_path（收敛到单一 shim）；
   - _conf_schema.json 字段类型均在 4.28.0 的 DEFAULT_VALUE_MAP 内，sync_secret 已掩码；
   - 全插件 `from astrbot...` 用到的模块/符号都在 4.28.0 已核验白名单内。
2. 真实面（子进程，需完整核心依赖，缺失时按跳过）：
   - 真实 astrbot 下 Star 自动注册、handler 进注册表、版本门禁通过、
     initialize/terminate 生命周期跑通（数据目录经 ASTRBOT_ROOT 隔离到临时目录）。
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# <core>/data/plugins/<plugin> → 上溯三层为 AstrBot 核心仓库根
CORE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(PLUGIN_ROOT)))
_PRIVATE_PATH_MODULE = "astrbot.core.utils.astrbot_path"
_SHIM_REL = "utils/astrbot_paths.py"

# AstrBot 4.28.0 core/config/default.py:DEFAULT_VALUE_MAP 支持的字段类型
_HOST_SCHEMA_TYPES = {
    "int", "float", "bool", "string", "text", "list",
    "file", "object", "template_list", "dict",
}

# 已逐符号核验存在于 AstrBot 4.28.0 的公开 API 白名单：模块 -> 允许的符号
_SYMBOL_ALLOWLIST = {
    "astrbot.api": {"logger"},
    "astrbot.api.event": {"AstrMessageEvent", "MessageChain", "MessageEventResult", "filter"},
    "astrbot.api.event.filter": {"EventMessageType"},
    "astrbot.api.platform": {"MessageType"},
    "astrbot.api.message_components": {"At", "AtAll", "Plain", "Node", "Nodes"},
    "astrbot.api.star": {"Context", "Star"},
}

_FROM_RE = re.compile(r"from\s+(astrbot[\w.]*)\s+import\s+(\([^)]*\)|[^\n(]+)")
_IMPORT_RE = re.compile(r"^import\s+(astrbot[\w.]*)", re.MULTILINE)


def _plugin_py_files():
    """返回插件生产代码（排除 tests/）的全部 .py 路径。"""
    out = []
    for root, dirs, files in os.walk(PLUGIN_ROOT):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", ".ruff_cache")]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rel(path):
    return os.path.relpath(path, PLUGIN_ROOT).replace("\\", "/")


def _parse_version(spec):
    """极简 PEP 440 解析：仅支持 >=,<=,>,<,==,!= 逗号连接（足够表达 >=4,<5）。"""
    parts = []
    for token in spec.split(","):
        token = token.strip()
        m = re.match(r"^(>=|<=|==|!=|>|<)\s*(.+)$", token)
        assert m, f"unsupported version specifier: {token!r}"
        parts.append((m.group(1), tuple(int(x) for x in m.group(2).split("."))))
    return parts


def _satisfies(version, spec):
    v = tuple(int(x) for x in version.split("."))
    for op, ref in _parse_version(spec):
        n = max(len(v), len(ref))
        a = v + (0,) * (n - len(v))
        b = ref + (0,) * (n - len(ref))
        if op == ">=" and not a >= b:
            return False
        if op == "<=" and not a <= b:
            return False
        if op == ">" and not a > b:
            return False
        if op == "<" and not a < b:
            return False
        if op == "==" and not a == b:
            return False
        if op == "!=" and not a != b:
            return False
    return True


async def test_metadata_version_gate():
    """metadata.yaml 版本门禁须覆盖 4.28.0，并封顶 <5 以防未来大版本静默加载。"""
    meta = _read(os.path.join(PLUGIN_ROOT, "metadata.yaml"))
    m = re.search(r'^astrbot_version:\s*"?([^"\n]+)"?', meta, re.MULTILINE)
    assert m, "metadata.yaml 缺少 astrbot_version"
    spec = m.group(1).strip()
    assert _satisfies("4.28.0", spec), f"4.28.0 不满足 {spec}"
    assert not _satisfies("5.0.0", spec), f"版本门禁未封顶 5.x：{spec}"
    for v in ("4.26.8", "4.27.0", "4.28.0"):
        assert _satisfies(v, spec), f"{v} 不满足 {spec}"
    # 必需的 metadata 字段齐备（自动注册后不再依赖 @register 传参）
    for field in ("name", "display_name", "desc", "version", "author"):
        assert re.search(rf"^{field}:", meta, re.MULTILINE), f"metadata.yaml 缺少 {field}"
    return f"版本门禁 {spec}：覆盖 4.26.8~4.28.0，封顶 5.x"


async def test_no_deprecated_register():
    """不得再使用已废弃的 @register 装饰器或从 astrbot.api.star 导入 register。"""
    reg_import = re.compile(r"from\s+astrbot\.api\.star\s+import[^\n]*\bregister\b")
    for path in _plugin_py_files():
        src = _read(path)
        assert not reg_import.search(src), f"{_rel(path)} 仍从 astrbot.api.star 导入 register"
        assert "@register" not in src, f"{_rel(path)} 仍使用 @register 装饰器"
    return "@register 已移除，改用 Star 子类自动注册"


async def test_no_private_core_imports():
    """私有模块 astrbot.core.utils.astrbot_path 只允许出现在单一 shim 内。"""
    shim = _SHIM_REL
    offenders = []
    for path in _plugin_py_files():
        if _read(path).find("astrbot.core.utils.astrbot_path") != -1 and _rel(path) != shim:
            offenders.append(_rel(path))
    assert not offenders, f"以下文件仍直接导入私有 core 路径 API: {offenders}"
    shim_src = _read(os.path.join(PLUGIN_ROOT, shim))
    assert "get_astrbot_plugin_data_path" in shim_src and "get_astrbot_data_path" in shim_src, (
        "shim 缺少核心路径回退链"
    )
    # 两个消费方必须改用 shim
    assert "get_plugin_data_dir" in _read(os.path.join(PLUGIN_ROOT, "db", "connection.py"))
    assert "get_plugin_data_dir" in _read(os.path.join(PLUGIN_ROOT, "services", "command_map.py"))
    return f"私有 core 导入收敛至 {shim}，两处消费方改用 get_plugin_data_dir"


async def test_schema_host_compat():
    """schema 字段类型须被 4.28.0 支持；sync_secret 须为掩码字段。"""
    schema = json.loads(_read(os.path.join(PLUGIN_ROOT, "_conf_schema.json")))
    bad = {k: v.get("type") for k, v in schema.items() if v.get("type") not in _HOST_SCHEMA_TYPES}
    assert not bad, f"存在 4.28.0 不支持的字段类型: {bad}"
    secret = schema.get("sync_secret")
    assert secret is not None, "schema 缺少 sync_secret"
    assert secret.get("secret") is True, "sync_secret 未启用 4.28.0 掩码（secret: true）"
    assert secret.get("type") == "string", "sync_secret 类型应为 string"
    return f"schema {len(schema)} 项类型全部受支持；sync_secret 已掩码"


async def test_import_surface_allowlist():
    """全插件用到的 astrbot 模块/符号必须落在 4.28.0 已核验白名单内。"""
    violations = []
    for path in _plugin_py_files():
        src = _read(path)
        rel = _rel(path)
        for mod, raw in _FROM_RE.findall(src):
            if mod == _PRIVATE_PATH_MODULE and rel == _SHIM_REL:
                continue  # 私有路径 API 的唯一合法落点
            if mod not in _SYMBOL_ALLOWLIST:
                violations.append(f"{rel}: 未核验模块 `{mod}`")
                continue
            body = raw.strip().strip("()")
            for item in body.split(","):
                item = item.split(" as ")[0].strip()
                if not item:
                    continue
                if item not in _SYMBOL_ALLOWLIST[mod]:
                    violations.append(f"{_rel(path)}: `{mod}.{item}` 不在白名单")
        for mod in _IMPORT_RE.findall(src):
            if mod not in _SYMBOL_ALLOWLIST:
                violations.append(f"{_rel(path)}: 未核验模块 `{mod}`")
    assert not violations, "发现未核验的 astrbot API 用法:\n  " + "\n  ".join(sorted(set(violations)))
    return f"扫描 {len(_plugin_py_files())} 个生产文件，astrbot API 用法全部在白名单内"


async def test_real_host_load():
    """真实 AstrBot 宿主加载冒烟（子进程；核心依赖缺失时跳过）。"""
    child = os.path.join(PLUGIN_ROOT, "tests", "host_smoke_child.py")
    env = dict(os.environ)
    env["ASTRBOT_CORE"] = CORE_ROOT
    env["PLUGIN_PARENT"] = os.path.dirname(PLUGIN_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    # 关键安全隔离：子进程会真实执行 initialize() 并建库，必须把 AstrBot 根目录
    # 指向临时目录，确保落盘的是临时 plugin_data，绝不触碰生产 points_system.db。
    with tempfile.TemporaryDirectory(prefix="astrbot_host_smoke_") as tmp_root:
        env["ASTRBOT_ROOT"] = tmp_root
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, child],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=tmp_root,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError("真实宿主加载子进程超时（180s）")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode == 3:
        return f"跳过（本机无完整 AstrBot 运行环境）：{err.splitlines()[-1] if err else 'deps unavailable'}"
    if proc.returncode != 0:
        raise AssertionError(f"真实宿主加载失败（exit={proc.returncode}）\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    assert "REAL HOST LOAD OK" in out, f"子进程未报告成功:\n{out}"
    return "真实 AstrBot 4.28.0：自动注册/handler/版本门禁/生命周期 全部通过"


TESTS = [
    ("metadata 版本门禁（>=4,<5）", test_metadata_version_gate),
    ("@register 废弃项已移除", test_no_deprecated_register),
    ("私有 core 路径导入已收敛", test_no_private_core_imports),
    ("配置 schema 与密钥掩码兼容", test_schema_host_compat),
    ("astrbot API 用法白名单", test_import_surface_allowlist),
    ("真实宿主加载冒烟（可跳过）", test_real_host_load),
]
