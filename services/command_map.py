"""Command map (help poster) catalog, data builders and long-lived image cache.

The command map is rendered into an image by the handler on demand.  The
cache stores the rendered image locally, keyed by a signature of
(catalog + runtime config subset + plugin version), so config or version
changes automatically force a re-render on the next trigger.
"""

import asyncio
import hashlib
import html
import json
import os
import shutil
import time
from pathlib import Path

from astrbot.api import logger

from ..config import defaults as _cfg_defaults

_CACHE_TTL = 86400.0
"""Default cache entry lifetime in seconds (24h)."""

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
"""Maximum cached image size; larger images are served without caching."""

_IMG_EXT = ".png"

_COMMAND_SECTIONS = [
    {
        "id": "member_noprefix",
        "title": "成员指令 · 无前缀触发",
        "badge": "keyword",
        "entries": [
            {
                "trigger": "keyword",
                "name": "{sign_kw}",
                "usage": "签到",
                "desc": "每日签到得积分，回复附今日运势",
            },
            {
                "trigger": "keyword",
                "name": "{lottery_kw} 抽奖（需口令 {passphrase}）",
                "usage": "{passphrase}抽奖",
                "desc": "口令抽奖，五档权重奖励",
            },
            {
                "trigger": "keyword",
                "name": "排行 / 排名 / 积分榜",
                "usage": "排行",
                "desc": "群内积分排行 Top10，人数不足自动回退全局榜",
            },
            {
                "trigger": "keyword",
                "name": "我的积分 / 积分查询",
                "usage": "我的积分",
                "desc": "查询我的当前积分 / 累计签到 / 连签 / 今日签到 / 本群排名 / 最近流水",
            },
        ],
    },
    {
        "id": "member_cmd",
        "title": "成员指令 · 斜杠指令",
        "badge": "command",
        "entries": [
            {
                "trigger": "command",
                "name": "/兑换",
                "usage": "/兑换",
                "desc": "查看可兑换物品列表",
            },
            {
                "trigger": "command",
                "name": "/兑换 <物品ID> [数量]",
                "usage": "/兑换 1",
                "desc": "兑换物品，库存原子扣减",
            },
            {
                "trigger": "command",
                "name": "/兑换记录 [页码 | 记录编号]",
                "usage": "/兑换记录 2",
                "desc": "查看自己的兑换记录",
            },
            {
                "trigger": "command",
                "name": "/流水 [页码 | all/全部 | @用户] [页码]",
                "usage": "/流水 3",
                "desc": "查看积分流水；all/全部 / @用户 仅管理员",
            },
            {
                "trigger": "command",
                "name": "/签到统计",
                "usage": "/签到统计",
                "desc": "今日签到人数 / 签到率 / 首签 / 连签王",
            },
            {
                "trigger": "command",
                "name": "/设置生日 <MM-DD>",
                "usage": "/设置生日 08-15",
                "desc": "设置生日，生日当天签到额外奖励",
            },
            {
                "trigger": "command",
                "name": "/查生日 [@用户]",
                "usage": "/查生日",
                "desc": "查看自己或指定用户的生日",
            },
            {
                "trigger": "command",
                "name": "/积分系统帮助",
                "usage": "/积分系统帮助",
                "desc": "生成本指令图（别名：指令图 / 命令图 / 帮助图）",
            },
        ],
    },
    {
        "id": "admin",
        "title": "管理员指令",
        "badge": "admin",
        "perm": "admin",
        "entries": [
            {
                "trigger": "command",
                "name": "/加分 <@用户/Q号> <分值>",
                "usage": "/加分 @小明 50",
                "desc": "增加积分",
            },
            {
                "trigger": "command",
                "name": "/扣分 <@用户/Q号> <分值>",
                "usage": "/扣分 @小明 10",
                "desc": "扣除积分（可扣成负数，扣负后自动分配负分头衔）",
            },
            {
                "trigger": "command",
                "name": "/添加兑换 <名称> <消耗> [库存]",
                "usage": "/添加兑换 纪念徽章 200 10",
                "desc": "新增兑换物品",
            },
            {
                "trigger": "command",
                "name": "/删除兑换 <物品ID>",
                "usage": "/删除兑换 3",
                "desc": "软删除物品",
            },
            {
                "trigger": "command",
                "name": "/修改兑换 <ID> <字段> <值>",
                "usage": "/修改兑换 3 价格 150",
                "desc": "修改物品属性（价格/库存/折扣价/折扣时间/名称/描述）",
            },
            {
                "trigger": "command",
                "name": "/核销 [通过|驳回] <记录编号> [备注]",
                "usage": "/核销 驳回 R20260101-0001 无货",
                "desc": "核销/驳回兑换订单并 @ 通知兑换者（未写动作默认通过，驳回退回积分）",
            },
            {
                "trigger": "command",
                "name": "/设置折扣 <ID> <折扣价> <截止时间>",
                "usage": "/设置折扣 1 100 2026-08-31 23:59",
                "desc": "设置兑换限时折扣",
            },
            {
                "trigger": "command",
                "name": "/清除折扣 <ID>",
                "usage": "/清除折扣 1",
                "desc": "清除兑换折扣",
            },
            {
                "trigger": "command",
                "name": "/设置今日口令 <关键词> <积分>",
                "usage": "/设置今日口令 红包 10",
                "desc": "设置每日口令，命中即得积分",
            },
            {
                "trigger": "command",
                "name": "/清除今日口令",
                "usage": "/清除今日口令",
                "desc": "清除每日口令",
            },
            {
                "trigger": "command",
                "name": "/设置 <配置项> <值>",
                "usage": "/设置 signin_fixed_points 15",
                "desc": "修改运行时配置，热生效",
            },
            {
                "trigger": "command",
                "name": "/查看配置",
                "usage": "/查看配置",
                "desc": "查看当前配置",
            },
            {
                "trigger": "command",
                "name": "/兑换记录 all/全部 | pending/未核销 [页码]",
                "usage": "/兑换记录 全部",
                "desc": "查看全部 / 未核销兑换记录（仅管理员）",
            },
            {
                "trigger": "command",
                "name": "/流水 <@用户 | all/全部> [页码]",
                "usage": "/流水 全部",
                "desc": "查看指定用户 / 全群流水（仅管理员）",
            },
            {
                "trigger": "command",
                "name": "/添加管理 <QQ号>",
                "usage": "/添加管理 10001",
                "desc": "添加本群积分管理员（仅群主 / 全局管理员）",
            },
            {
                "trigger": "command",
                "name": "/删除管理 <QQ号>",
                "usage": "/删除管理 10001",
                "desc": "移除本群积分管理员（仅群主 / 全局管理员）",
            },
            {
                "trigger": "command",
                "name": "/添加日期奖励 <范围> <关键词> <积分> [概率]",
                "usage": "/添加日期奖励 08-15~08-17 中秋 66 0.5",
                "desc": "新增日期奖励，支持单日 / 跨年区间",
            },
            {
                "trigger": "command",
                "name": "/删除日期奖励 <ID>",
                "usage": "/删除日期奖励 2",
                "desc": "软删除日期奖励",
            },
            {
                "trigger": "command",
                "name": "/查看日期奖励",
                "usage": "/查看日期奖励",
                "desc": "查看日期奖励列表",
            },
            {
                "trigger": "command",
                "name": "/清空数据",
                "usage": "/清空数据",
                "desc": "清空本群数据，验证码二次确认，清空前自动备份",
            },
            {
                "trigger": "command",
                "name": "/清空全部数据",
                "usage": "/清空全部数据",
                "desc": "清空全部数据（仅全局管理员），验证码二次确认",
            },
            {
                "trigger": "command",
                "name": "/确认清空 <验证码>",
                "usage": "/确认清空 123456",
                "desc": "确认执行待清空操作（5 分钟内有效，单次有效）",
            },
        ],
    },
]

_POSTER_OPTIONS = {"type": "png", "quality": 90}

# Self-contained poster template: inline CSS only, no external resources.
_POSTER_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>积分系统指令图 {{ version }}</title>
<style>
  body { font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; margin: 0; color: #2c3e50; background: #f6f8fa; }
  .header { background: linear-gradient(135deg, #3eaf7c, #2c855d); color: #fff; padding: 26px 36px; }
  .header h1 { margin: 0; font-size: 34px; letter-spacing: 1px; }
  .header .sub { margin-top: 8px; font-size: 15px; opacity: 0.92; }
  .wrap { padding: 24px 32px 40px; }
  .section { background: #fff; border: 1px solid #e4e7ed; border-radius: 12px; margin-top: 22px; overflow: hidden; }
  .section-head { display: flex; align-items: center; gap: 12px; padding: 14px 22px; background: #f0f7f4; border-bottom: 1px solid #e4e7ed; }
  .section-head h2 { margin: 0; font-size: 21px; }
  .badge { font-size: 13px; font-weight: 600; padding: 3px 10px; border-radius: 999px; color: #fff; }
  .badge-keyword { background: #3eaf7c; }
  .badge-command { background: #4a90e2; }
  .badge-admin { background: #e67e22; }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 11px 22px; border-bottom: 1px solid #f0f2f5; vertical-align: top; font-size: 15px; line-height: 1.55; }
  tr:last-child td { border-bottom: none; }
  td.cmd { width: 36%; font-weight: 600; }
  td.usage { width: 32%; font-family: Menlo, Consolas, monospace; font-size: 13.5px; color: #3eaf7c; }
  td.desc { color: #5c6773; }
  .footer { text-align: center; color: #98a2ad; font-size: 13px; padding: 18px 0 6px; }
</style>
</head>
<body>
  <div class="header">
    <h1>积分系统 · 指令图</h1>
    <div class="sub">AstrBot 积分系统插件 {{ version }} — 完整指令一览</div>
  </div>
  <div class="wrap">
    {% for section in sections %}
    <div class="section">
      <div class="section-head">
        <span class="badge badge-{{ section.badge }}">{{ section.title }}</span>
      </div>
      <table>
        {% for entry in section.entries %}
        <tr>
          <td class="cmd">{{ entry.name }}</td>
          <td class="usage">{{ entry.usage }}</td>
          <td class="desc">{{ entry.desc }}</td>
        </tr>
        {% endfor %}
      </table>
    </div>
    {% endfor %}
    <div class="footer">Powered by AstrBot · 积分系统 for WHL</div>
  </div>
</body>
</html>
"""


def _fill(template: str, sign_text: str, lottery_text: str, passphrase: str) -> str:
    """Substitute runtime keyword placeholders into a catalog entry string."""
    return (
        template.replace("{sign_kw}", sign_text)
        .replace("{lottery_kw}", lottery_text)
        .replace("{passphrase}", passphrase)
    )


def build_map_data(config_cache: dict) -> dict:
    """Build the full command map data with runtime keywords injected.

    Args:
        config_cache: The plugin config cache (already parsed list keys).

    Returns:
        A dict with the plugin version and the command sections/entries.
    """
    sign_kw = config_cache.get("keyword_sign", []) or ["签到"]
    lottery_kw = config_cache.get("keyword_lottery", []) or ["抽奖"]
    passphrase = str(config_cache.get("lottery_passphrase", "whl"))
    sign_text = " / ".join(str(k) for k in sign_kw)
    lottery_text = " / ".join(str(k) for k in lottery_kw)
    sections = []
    for section in _COMMAND_SECTIONS:
        entries = []
        for entry in section["entries"]:
            entries.append(
                {
                    **entry,
                    "name": _fill(entry["name"], sign_text, lottery_text, passphrase),
                    "usage": _fill(entry["usage"], sign_text, lottery_text, passphrase),
                }
            )
        sections.append({**section, "entries": entries})
    return {"version": _cfg_defaults.PLUGIN_VERSION, "sections": sections}


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def poster_data(data: dict) -> dict:
    """Build HTML-escaped poster template data from the map data.

    Args:
        data: Output of build_map_data().

    Returns:
        Template data with all strings HTML-escaped.
    """
    return {
        "version": _esc(data["version"]),
        "sections": [
            {
                "title": _esc(section["title"]),
                "badge": _esc(section["badge"]),
                "entries": [
                    {
                        "name": _esc(entry["name"]),
                        "usage": _esc(entry["usage"]),
                        "desc": _esc(entry["desc"]),
                    }
                    for entry in section["entries"]
                ],
            }
            for section in data["sections"]
        ],
    }


def build_markdown(data: dict) -> str:
    """Build the markdown command table (used for t2i / plain text fallback)."""
    lines = [
        "# 积分系统指令总览",
        "",
        f"插件版本：{data['version']}",
    ]
    for section in data["sections"]:
        lines += [
            "",
            f"## {section['title']}",
            "",
            "| 指令 | 用法 | 说明 |",
            "|---|---|---|",
        ]
        for entry in section["entries"]:
            name = entry["name"].replace("|", "\\|")
            usage = entry["usage"].replace("|", "\\|")
            desc = entry["desc"].replace("|", "\\|")
            lines.append(f"| {name} | `{usage}` | {desc} |")
    return "\n".join(lines)


def cache_signature(data: dict) -> str:
    """Compute the cache key from map data (includes the plugin version).

    Args:
        data: Output of build_map_data().

    Returns:
        First 16 hex chars of the SHA-256 digest of the sorted JSON payload.
    """
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def cache_dir() -> Path:
    """Resolve the command map cache directory under the plugin data path."""
    base = None
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = get_astrbot_plugin_data_path()
    except Exception:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = get_astrbot_data_path()
        except Exception:
            base = os.getcwd()
    return Path(base) / "astrbot_plugin_point_system_by_whleague" / "command_map"


class CommandMapCache:
    """Long-lived on-disk cache for rendered command map images.

    Args:
        cache_path: Cache directory; created if missing.
    """

    def __init__(self, cache_path: Path | None = None):
        self._dir = Path(cache_path) if cache_path else cache_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @property
    def dir(self) -> Path:
        return self._dir

    def get(self, key: str, ttl_seconds: float = _CACHE_TTL) -> str | None:
        """Return the cached image path for the key, or None on miss.

        Args:
            key: Cache key (signature).
            ttl_seconds: Entry lifetime in seconds; entries older than the
                TTL are treated as expired and removed. A non-positive TTL
                disables the cache (always miss).

        Returns:
            The cached image path, or None when absent or expired.
        """
        if ttl_seconds <= 0:
            return None
        path = self._mem.get(key)
        if not path:
            return None
        p = Path(path)
        try:
            if p.stat().st_mtime < time.time() - ttl_seconds:
                p.unlink(missing_ok=True)
                self._mem.pop(key, None)
                return None
        except OSError:
            self._mem.pop(key, None)
            return None
        return path

    def store(
        self, key: str, src_path: str, ttl_seconds: float = _CACHE_TTL
    ) -> str | None:
        """Move a freshly rendered image into the cache.

        Args:
            key: Cache key (signature).
            src_path: Local path of the rendered image.
            ttl_seconds: Entry lifetime in seconds; a non-positive TTL
                disables caching and serves the original path directly.

        Returns:
            The path to send to the user (cached copy when under the size
            limit, otherwise the original path), or None when unusable.
        """
        src = Path(src_path)
        if not src.is_file():
            return None
        try:
            size = src.stat().st_size
        except OSError:
            return None
        if ttl_seconds <= 0:
            return str(src)
        if size > _MAX_IMAGE_BYTES:
            logger.warning(
                f"Command map image too large ({size} bytes); serving without cache."
            )
            return str(src)
        dest = self._dir / f"{key}{_IMG_EXT}"
        tmp = dest.with_suffix(".tmp")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        except OSError as e:
            logger.warning(f"Failed to store command map cache: {e}")
            return str(src)
        self._mem[key] = str(dest)
        return str(dest)

    def sweep(self, ttl_seconds: float = _CACHE_TTL) -> None:
        """Remove cache files older than the TTL (best-effort).

        Args:
            ttl_seconds: Entry lifetime in seconds; falls back to the
                default TTL when non-positive.
        """
        if ttl_seconds <= 0:
            ttl_seconds = _CACHE_TTL
        cutoff = time.time() - ttl_seconds
        try:
            for path in self._dir.glob(f"*{_IMG_EXT}"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError as e:
            logger.warning(f"Command map cache sweep failed: {e}")
