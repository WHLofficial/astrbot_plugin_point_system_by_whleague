"""竞猜同步通道本地联调入口：脱离 AstrBot 单独运行插件的 HTTP 服务与战报轮询。

用法（插件根目录）：
    python -m tests.sync_standalone --secret testsecret --port 9991
可选参数：
    --host 127.0.0.1        监听地址
    --base-url URL          竞猜系统地址（默认 https://guess.whleague.win）
    --groups 111,222        战报转发目标群（默认空：拉到战报也不转发不确认）
    --db 路径               指定 SQLite 库文件（默认临时库，退出即弃）
    --poll 60               战报轮询间隔秒（最小 15）

用途：配合竞猜系统仓库 scripts/smoke-test.sh 本地联调（SYNC_BASE_URL=http://127.0.0.1:9991）。
安全声明：默认使用 tempfile 临时库，绝不触碰生产 points_system.db；SECRET 只用于签名，
不打印、不回显。联调模式下群消息发送仅打印日志并返回失败（因此不会 ack 战报），
避免把未真正投递到群的消息标记为已发送。
"""

import argparse
import asyncio
import sys
import types

# 导入即安装 astrbot 桩并修正 sys.path（须先于任何插件模块导入）
from .common import TempDB, base_cfg


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="竞猜同步通道联调服务（无 AstrBot 运行时）")
    p.add_argument("--secret", required=True, help="SYNC_SECRET（与竞猜系统一致）")
    p.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址")
    p.add_argument("--port", type=int, default=9991, help="HTTP 监听端口")
    p.add_argument(
        "--base-url", default="https://guess.whleague.win", help="竞猜系统 BASE 地址"
    )
    p.add_argument("--groups", default="", help="战报转发目标群号，逗号分隔（默认空）")
    p.add_argument("--db", default="", help="SQLite 库文件路径（默认临时库）")
    p.add_argument("--poll", type=int, default=60, help="战报轮询间隔秒")
    return p.parse_args()


class _EchoContext:
    """联调模式下的假 Context：群消息发送只打印日志并返回失败（不 ack）。"""

    async def send_message(self, origin, chain):
        print(f"[联调] （假发送）{origin}: {chain} —— 返回失败，本轮不 ack")
        return False


async def _run(args: argparse.Namespace) -> None:
    from astrbot_plugin_point_system_by_whleague.db.connection import DatabaseManager
    from astrbot_plugin_point_system_by_whleague.db.dao import PointDAO
    from astrbot_plugin_point_system_by_whleague.db.schema import init_schema
    from astrbot_plugin_point_system_by_whleague.db.sync_dao import SyncDAO
    from astrbot_plugin_point_system_by_whleague.handlers.sync import SyncHandler
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.services.sync_service import SyncService
    from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import RateLimiter

    temp: TempDB | None = None
    if args.db:
        db = DatabaseManager(args.db)
        await db.init()
        await init_schema(db)
        db_display = args.db
    else:
        temp = TempDB()
        await temp.__aenter__()
        db = temp.db  # TempDB.__aenter__ 返回自身，真正的 DatabaseManager 在 .db
        db_display = f"{temp.path}（临时，退出即弃）"
    try:
        plugin = types.SimpleNamespace(
            config_cache=base_cfg(
                sync_enabled=True,
                sync_secret=args.secret,
                sync_base_url=args.base_url,
                sync_listen_host=args.host,
                sync_listen_port=args.port,
                sync_report_groups=[g.strip() for g in args.groups.split(",") if g.strip()],
                sync_poll_interval=max(15, args.poll),
                sync_platform_id="aiocqhttp",
            ),
            rate_limiter=RateLimiter(),
            context=_EchoContext(),
        )
        plugin.sync_service = SyncService(db, SyncDAO(db), PointService(db, PointDAO(db)))
        handler = SyncHandler(plugin)
        poll_task: asyncio.Task | None = None
        try:
            if not await handler.start():
                print("[联调] HTTP 服务启动失败，退出")
                return
            poll_task = asyncio.create_task(handler.poll_loop())
            print("=" * 56)
            print(f"[联调] 监听      http://{args.host}:{args.port}")
            print(f"[联调] 竞猜系统  {args.base_url}")
            print(f"[联调] 数据库    {db_display}")
            print(f"[联调] 战报群    {plugin.config_cache['sync_report_groups'] or '(未配置)'}")
            print(f"[联调] 验证：  curl -i http://{args.host}:{args.port}/sync/summary  应得 401")
            print("[联调] Ctrl-C 退出；SECRET 已加载（不回显）")
            print("=" * 56)
            await asyncio.Future()  # 运行直到取消
        finally:
            if poll_task is not None:
                poll_task.cancel()
                await asyncio.gather(poll_task, return_exceptions=True)
            await handler.stop()
    finally:
        if temp is not None:
            await temp.__aexit__(None, None, None)
        else:
            await db.close()


def main() -> None:
    args = _parse()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n[联调] 已退出")


if __name__ == "__main__":
    sys.exit(main())
