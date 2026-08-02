import asyncio
import json
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star, register

from .config.defaults import (
    _LIST_KEYS,
    DEFAULT_CONFIG,
    PLUGIN_VERSION,
    parse_keyword_list,
)
from .db.connection import DatabaseManager
from .db.dao import PointDAO
from .db.schema import init_schema
from .utils.helpers import set_day_boundary, today_str
from .utils.rate_limiter import RateLimiter

_PLUGIN_COMMANDS = frozenset({
    "\u7b7e\u5230\u7edf\u8ba1", "\u5151\u6362", "\u5151\u6362\u8bb0\u5f55", "\u6838\u9500",
    "\u6dfb\u52a0\u5151\u6362", "\u5220\u9664\u5151\u6362", "\u4fee\u6539\u5151\u6362",
    "\u52a0\u5206", "\u6263\u5206", "\u8bbe\u7f6e\u4eca\u65e5\u53e3\u4ee4", "\u6e05\u9664\u4eca\u65e5\u53e3\u4ee4",
    "\u8bbe\u7f6e", "\u67e5\u770b\u914d\u7f6e", "\u8bbe\u7f6e\u6298\u6263", "\u6e05\u9664\u6298\u6263",
    "\u6dfb\u52a0\u7ba1\u7406", "\u5220\u9664\u7ba1\u7406", "\u6dfb\u52a0\u65e5\u671f\u5956\u52b1",
    "\u5220\u9664\u65e5\u671f\u5956\u52b1", "\u67e5\u770b\u65e5\u671f\u5956\u52b1",
    "\u8bbe\u7f6e\u751f\u65e5", "\u67e5\u751f\u65e5", "\u6d41\u6c34",
    "\u6e05\u7a7a\u6570\u636e", "\u6e05\u7a7a\u5168\u90e8\u6570\u636e", "\u786e\u8ba4\u6e05\u7a7a",
    "\u79ef\u5206\u7cfb\u7edf\u5e2e\u52a9", "\u6307\u4ee4\u56fe", "\u547d\u4ee4\u56fe", "\u5e2e\u52a9\u56fe",
})


@register("points_system", "WHLofficial",
          "\u79ef\u5206\u7cfb\u7edf\u63d2\u4ef6\uff1a\u7b7e\u5230/\u62bd\u5956/\u5151\u6362/\u6392\u884c/\u751f\u65e5\u7b49", PLUGIN_VERSION)
class PointSystemPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config
        """AstrBot 托管的插件配置（WebUI 中可见、可修改），见 _conf_schema.json。"""

    async def initialize(self) -> None:
        self.db = DatabaseManager()
        await self.db.init()

        self.dao = PointDAO(self.db)

        await init_schema(self.db)

        self.config_cache = await self._load_config_cache()
        set_day_boundary(self.config_cache.get("signin_refresh_time", "04:00"))
        self.rate_limiter = RateLimiter()

        from .services.date_reward_service import DateRewardService
        from .services.easter_service import EasterService
        from .services.point_service import PointService

        self.point_service = PointService(self.db, self.dao)
        self.easter_service = EasterService(self.dao)
        self.date_reward_service = DateRewardService(self.dao)

        from .services.backup_service import BackupService
        from .services.birthday_service import BirthdayService
        from .services.daily_keyword_service import DailyKeywordService
        from .services.lottery_service import LotteryService
        from .services.ranking_service import RankingService
        from .services.redeem_service import RedeemService
        from .services.sign_in_service import SignInService

        self.sign_in_service = SignInService(
            self.db,
            self.dao,
            self.point_service,
            self.easter_service,
            self.date_reward_service,
            self.config_cache,
        )
        self.lottery_service = LotteryService(
            self.db, self.dao, self.point_service, self.config_cache
        )
        self.redeem_service = RedeemService(self.db, self.dao, self.point_service)
        self.ranking_service = RankingService(self.dao)
        self.daily_keyword_service = DailyKeywordService(
            self.db, self.dao, self.point_service
        )
        self.birthday_service = BirthdayService(self.dao)
        self.backup_service = BackupService(self.db, self.config_cache)

        from .handlers.active_reward import ActiveRewardHandler
        from .handlers.admin import AdminHandler
        from .handlers.birthday import BirthdayHandler
        from .handlers.lottery import LotteryHandler
        from .handlers.ranking import RankingHandler
        from .handlers.redeem import RedeemHandler
        from .handlers.sign_in import SignInHandler

        self.sign_in_handler = SignInHandler(self)
        self.lottery_handler = LotteryHandler(self)
        self.redeem_handler = RedeemHandler(self)
        self.ranking_handler = RankingHandler(self)
        self.admin_handler = AdminHandler(self)
        self.birthday_handler = BirthdayHandler(self)
        self.active_reward_handler = ActiveRewardHandler(self)

        from .handlers.command_map import CommandMapHandler

        self.command_map_handler = CommandMapHandler(self)
        self._cache_sweep_task = asyncio.create_task(
            self.command_map_handler.sweep_loop()
        )

        await self._start_cron_jobs()

        logger.info("Point system plugin initialized.")

    async def _load_config_cache(self) -> dict:
        """从 AstrBot 托管的配置（data/config/*_config.json）构建配置缓存。

        兼容旧版本：首次部署（配置文件刚生成）时，将旧版数据库 plugin_config
        表中的配置迁移到托管配置文件中，并清空旧表。
        """
        if self.config is None:
            cache = dict(DEFAULT_CONFIG)
            for key in _LIST_KEYS:
                cache[key] = parse_keyword_list(cache[key])
            return cache

        cache = {}
        for key, default in DEFAULT_CONFIG.items():
            val = self.config.get(key, default)
            if key in _LIST_KEYS:
                cache[key] = parse_keyword_list(val)
            else:
                cache[key] = val

        if getattr(self.config, "first_deploy", False):
            rows = await self.dao.get_all_config()
            legacy = {
                r["key"]: r["value"]
                for r in rows
                if r["key"] not in ("schema_version",)
            }
            changed = False
            for key, raw in legacy.items():
                if key not in DEFAULT_CONFIG:
                    continue
                try:
                    parsed = self._cast_config_value(key, raw)
                except (ValueError, TypeError):
                    logger.warning(
                        f"Invalid legacy config value for {key}: {raw!r}, skipped."
                    )
                    continue
                self.config[key] = parsed
                cache[key] = parsed
                changed = True
            if changed:
                self.config.save_config()
                logger.info("Migrated legacy DB config into plugin config file.")
            await self.dao.clear_config()
        return cache

    @staticmethod
    def _parse_hhmm(
        value: str, default_hour: int, default_minute: int
    ) -> tuple[int, int]:
        """解析 HH:MM 配置，非法值回退默认。"""
        try:
            hour, minute = value.strip().split(":", 1)
            hour, minute = int(hour), int(minute)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        except (AttributeError, ValueError, TypeError):
            pass
        return default_hour, default_minute

    @staticmethod
    def _cast_config_value(key: str, raw: str):
        if key in _LIST_KEYS:
            return parse_keyword_list(raw)
        default = DEFAULT_CONFIG[key]
        if isinstance(default, bool):
            return raw.lower() in ("true", "1", "yes")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
        return raw

    async def _remove_cron_jobs(self) -> None:
        """移除已注册的定时任务（配置热更新与终止时复用）。"""
        for attr in ("_backup_job", "_birthday_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    job.remove()
                except Exception:
                    pass
        self._backup_job = None
        self._birthday_job = None

    async def reschedule_cron_jobs(self) -> None:
        """热更新定时任务：移除旧任务后按最新配置重建。"""
        await self._remove_cron_jobs()
        await self._start_cron_jobs()

    async def _start_cron_jobs(self) -> None:
        try:
            cfg = self.config_cache

            if cfg.get("backup_enabled", True):
                backup_time = cfg.get("backup_time", "04:00")
                backup_hour, backup_minute = self._parse_hhmm(backup_time, 4, 0)
                job = await self.context.cron_manager.add_basic_job(
                    name="points_backup",
                    cron_expression=f"{backup_minute} {backup_hour} * * *",
                    handler=self._cron_backup,
                    description="Daily point system backup",
                )
                self._backup_job = job
                logger.info(
                    f"Backup cron job scheduled at {backup_time} (host local time)."
                )

            announce_time = cfg.get("birthday_announce_time", "08:00")
            hour, minute = self._parse_hhmm(announce_time, 8, 0)
            cron_expr = f"{minute} {hour} * * *"
            job2 = await self.context.cron_manager.add_basic_job(
                name="birthday_announce",
                cron_expression=cron_expr,
                handler=self._cron_birthday_announce,
                description="Daily birthday announcement",
            )
            self._birthday_job = job2
            logger.info(f"Birthday announce cron job scheduled at {announce_time}.")
        except Exception as e:
            logger.warning(f"Failed to schedule cron jobs: {e}")
            self._backup_job = None
            self._birthday_job = None

    async def _cron_backup(self) -> None:
        logger.info("Running scheduled backup...")
        await self.backup_service.run_backup()

    async def _cron_birthday_announce(self) -> None:
        logger.info("Running birthday announcement...")
        group_ids = await self.dao.get_all_group_ids()
        today = today_str()
        for gid in group_ids:
            try:
                result = await self.birthday_service.announce_birthdays(gid)
                if result.get("announced"):
                    users = result["users"]
                    platform = "aiocqhttp"
                    account = await self.dao.get_account(users[0])
                    if account and account["platform"]:
                        platform = account["platform"]
                    origin = f"{platform}:{MessageType.GROUP_MESSAGE.value}:{gid}"
                    try:
                        chain = MessageChain().message(
                            "\U0001f382 \u751f\u65e5\u795d\u798f\uff01\u4eca\u5929\u8fc7\u751f\u65e5\u7684\u670b\u53cb\u6709\uff1a"
                        )
                        for u in users:
                            chain.at(str(u), u)
                        chain.message(
                            "\n\u795d\u5927\u5bb6\u751f\u65e5\u5feb\u4e50\uff01"
                        )
                        await self.context.send_message(origin, chain)
                        await self.dao.mark_birthday_announced(
                            gid, today, json.dumps(users)
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send birthday msg to {gid}: {e}")
            except Exception as e:
                logger.error(f"Birthday announce error for group {gid}: {e}")

    # ═══════════════════════════════════════════════════════════
    # Handlers: Sign-in
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"(?:\u7b7e\u5230|\u6253\u5361|(?i:\bsign\b))")
    async def on_sign_in(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if event.is_at_or_wake_command:
            msg = event.get_message_str()
            for cmd in _PLUGIN_COMMANDS:
                if msg == cmd or msg.startswith(cmd + " "):
                    return
        async for result in self.sign_in_handler.handle(event):
            yield result
        event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Lottery
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"(?:\u62bd\u5956|(?i:\blottery\b))")
    async def on_lottery(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for result in self.lottery_handler.handle(event):
            yield result
        event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Ranking
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"\u6392\u884c|\u6392\u540d|\u79ef\u5206\u699c")
    async def on_ranking(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for result in self.ranking_handler.handle(event):
            yield result
        event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Redeem (command)
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u5151\u6362")
    async def cmd_redeem(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        msg = event.get_message_str()
        parts = msg.split()
        if len(parts) == 1:
            async for r in self.redeem_handler.list_items(event):
                yield r
            return
        if len(parts) == 2:
            async for r in self.redeem_handler.do_redeem(event, parts[1]):
                yield r
            return
        async for r in self.redeem_handler.do_redeem(event, parts[1], parts[2]):
            yield r

    @filter.command("\u5151\u6362\u8bb0\u5f55")
    async def cmd_redeem_records(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        msg = event.get_message_str()
        parts = msg.split(maxsplit=2)
        target = parts[1] if len(parts) >= 2 else None
        page = parts[2] if len(parts) >= 3 else "1"
        # 纯数字参数视为页码（与 /流水 语义一致），如 /兑换记录 2
        if target and target.isdigit():
            page = target
            target = None
        async for r in self.redeem_handler.list_records(event, target, page):
            yield r

    @filter.command("\u6838\u9500")
    async def cmd_verify(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        msg = event.get_message_str()
        parts = msg.split(maxsplit=2)
        if len(parts) < 2:
            yield event.plain_result(
                "\u7528\u6cd5: /\u6838\u9500 <\u8bb0\u5f55\u7f16\u53f7> [\u5907\u6ce8]"
            )
            return
        record_no = parts[1]
        note = parts[2] if len(parts) >= 3 else ""
        async for r in self.redeem_handler.toggle_verify(event, record_no, note):
            yield r

    @filter.command("\u6dfb\u52a0\u5151\u6362")
    async def cmd_add_item(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.add_item(event):
            yield r

    @filter.command("\u5220\u9664\u5151\u6362")
    async def cmd_delete_item(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.delete_item(event):
            yield r

    @filter.command("\u4fee\u6539\u5151\u6362")
    async def cmd_modify_item(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.modify_item(event):
            yield r

    # ═══════════════════════════════════════════════════════════
    # Handlers: Admin
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u52a0\u5206")
    async def cmd_add_points(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.adjust_points(event, "\u52a0\u5206"):
            yield r

    @filter.command("\u6263\u5206")
    async def cmd_sub_points(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.adjust_points(event, "\u6263\u5206"):
            yield r

    @filter.command("\u8bbe\u7f6e\u4eca\u65e5\u53e3\u4ee4")
    async def cmd_set_daily_kw(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.set_daily_kw(event):
            yield r

    @filter.command("\u6e05\u9664\u4eca\u65e5\u53e3\u4ee4")
    async def cmd_clear_daily_kw(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.clear_daily_kw(event):
            yield r

    @filter.command("\u8bbe\u7f6e")
    async def cmd_set_config(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.set_config(event):
            yield r

    @filter.command("\u67e5\u770b\u914d\u7f6e")
    async def cmd_view_config(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.view_config(event):
            yield r

    @filter.command("\u8bbe\u7f6e\u6298\u6263")
    async def cmd_set_discount(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.set_discount(event):
            yield r

    @filter.command("\u6e05\u9664\u6298\u6263")
    async def cmd_clear_discount(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.clear_discount(event):
            yield r

    @filter.command("\u6dfb\u52a0\u7ba1\u7406")
    async def cmd_add_admin(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.add_admin(event):
            yield r

    @filter.command("\u5220\u9664\u7ba1\u7406")
    async def cmd_remove_admin(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.remove_admin(event):
            yield r

    @filter.command("\u6dfb\u52a0\u65e5\u671f\u5956\u52b1")
    async def cmd_add_date_reward(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.add_date_reward(event):
            yield r

    @filter.command("\u5220\u9664\u65e5\u671f\u5956\u52b1")
    async def cmd_delete_date_reward(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.delete_date_reward(event):
            yield r

    @filter.command("\u67e5\u770b\u65e5\u671f\u5956\u52b1")
    async def cmd_view_date_rewards(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.view_date_rewards(event):
            yield r

    @filter.command("\u6e05\u7a7a\u6570\u636e")
    async def cmd_clear_data(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.clear_data(event, "group"):
            yield r

    @filter.command("\u6e05\u7a7a\u5168\u90e8\u6570\u636e")
    async def cmd_clear_all(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.clear_data(event, "global"):
            yield r

    @filter.command("\u786e\u8ba4\u6e05\u7a7a")
    async def cmd_confirm_clear(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.admin_handler.confirm_clear(event):
            yield r

    # ═══════════════════════════════════════════════════════════
    # Handlers: Command map
    # ═══════════════════════════════════════════════════════════

    @filter.command(
        "\u79ef\u5206\u7cfb\u7edf\u5e2e\u52a9",
        alias={"\u6307\u4ee4\u56fe", "\u547d\u4ee4\u56fe", "\u5e2e\u52a9\u56fe"},
    )
    async def cmd_command_map(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.command_map_handler.handle(event):
            yield r

    # ═══════════════════════════════════════════════════════════
    # Handlers: Birthday
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u8bbe\u7f6e\u751f\u65e5")
    async def cmd_set_birthday(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.birthday_handler.set_birthday(event):
            yield r

    @filter.command("\u67e5\u751f\u65e5")
    async def cmd_query_birthday(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.birthday_handler.query_birthday(event):
            yield r

    # ═══════════════════════════════════════════════════════════
    # Handlers: Stats
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u7b7e\u5230\u7edf\u8ba1")
    async def cmd_signin_stats(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self.ranking_handler.stats(event):
            yield r

    @filter.command("\u6d41\u6c34")
    async def cmd_transactions(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        qq = event.get_sender_id()
        group_id = event.get_group_id()
        msg = event.get_message_str()
        parts = msg.split(maxsplit=2)
        from .utils.security import parse_qq_arg

        is_admin = event.is_admin() or await self.dao.is_admin(qq, group_id)
        target_qq = qq
        page = 1
        if len(parts) >= 2:
            if parts[1] == "all":
                if not is_admin:
                    yield event.plain_result(
                        "\u4f60\u6ca1\u6709\u6743\u9650\u67e5\u770b\u4ed6\u4eba\u6d41\u6c34"
                    )
                    return
                target_qq = None
            else:
                parsed_target = parse_qq_arg(parts[1])
                if parsed_target is not None:
                    if not is_admin:
                        yield event.plain_result(
                            "\u4f60\u6ca1\u6709\u6743\u9650\u67e5\u770b\u4ed6\u4eba\u6d41\u6c34"
                        )
                        return
                    target_qq = parsed_target
                elif parts[1].isdigit():
                    page = max(1, int(parts[1]))
                else:
                    yield event.plain_result(
                        "\u53c2\u6570\u9519\u8bef: \u7528\u6cd5 / \u6d41\u6c34 [\u9875\u7801 | all | @\u7528\u6237] [\u9875\u7801]"
                    )
                    return
            if len(parts) >= 3 and parts[2].isdigit():
                page = max(1, int(parts[2]))

        offset = (page - 1) * 10
        records = await self.dao.get_transactions(
            qq=target_qq, group_id=group_id, limit=10, offset=offset
        )
        if not records:
            yield event.plain_result("\u6ca1\u6709\u6d41\u6c34\u8bb0\u5f55")
            return
        lines = [f"\U0001f4ca \u79ef\u5206\u6d41\u6c34 (\u7b2c{page}\u9875)"]
        for r in records:
            icon = "\U0001f7e2" if r["amount"] >= 0 else "\U0001f534"
            lines.append(
                f"{icon} {r['amount']:+d}  {r['reason']}  {r['created_at'][:16]}"
            )
        yield event.plain_result("\n".join(lines))

    # ═══════════════════════════════════════════════════════════
    # Handlers: Active reward (intercepts all group messages)
    # ═══════════════════════════════════════════════════════════

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        await self.active_reward_handler.handle(event)

    # ═══════════════════════════════════════════════════════════
    # Teardown
    # ═══════════════════════════════════════════════════════════

    async def terminate(self) -> None:
        if hasattr(self, "_cache_sweep_task") and self._cache_sweep_task:
            try:
                self._cache_sweep_task.cancel()
            except Exception:
                pass
        await self._remove_cron_jobs()
        if hasattr(self, "db"):
            await self.db.close()
        logger.info("Point system plugin terminated.")
