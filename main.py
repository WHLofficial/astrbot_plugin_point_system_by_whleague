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
from .utils.keyword_matcher import (
    is_lottery_message,
    is_my_points_message,
    is_ranking_message,
    is_signin_message,
)
from .utils.rate_limiter import RateLimiter


@register(
    "points_system",
    "WHLofficial",
    "\u79ef\u5206\u7cfb\u7edf\u63d2\u4ef6\uff1a\u7b7e\u5230/\u62bd\u5956/\u5151\u6362/\u6392\u884c/\u751f\u65e5\u7b49",
    PLUGIN_VERSION,
)
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

        # 清理历史遗留的非持久化 cron 任务行（旧版本禁用/热更新插件时未真正移除，
        # 残留行虽不会被重新调度但会持续累积；幂等自愈）。
        await self._cleanup_stale_cron_jobs()

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

        from .services.rob_service import RobService

        self.rob_service = RobService(
            self.db,
            self.dao,
            self.point_service,
            self.config_cache,
            self.rate_limiter,
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
        from .handlers.my_points import MyPointsHandler
        from .handlers.ranking import RankingHandler
        from .handlers.redeem import RedeemHandler
        from .handlers.rob import RobHandler
        from .handlers.sign_in import SignInHandler

        self.sign_in_handler = SignInHandler(self)
        self.lottery_handler = LotteryHandler(self)
        self.redeem_handler = RedeemHandler(self)
        self.ranking_handler = RankingHandler(self)
        self.admin_handler = AdminHandler(self)
        self.birthday_handler = BirthdayHandler(self)
        self.active_reward_handler = ActiveRewardHandler(self)
        self.my_points_handler = MyPointsHandler(self)
        self.rob_handler = RobHandler(self)

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
            elif key == "lottery_tiers":
                cache[key] = self._sanitize_lottery_tiers(val)
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

        # 动态方案基准最小 1：WebUI 输入 0 按 1 处理并回写持久化
        # （/设置 路径已在 handler 拒绝，此处兜底 WebUI/手改配置）
        if (
            cache.get("rob_target_limit_dynamic")
            and cache.get("rob_target_daily_limit", 0) < 1
        ):
            logger.warning(
                "rob_target_limit_dynamic=true 时 rob_target_daily_limit 不能为 0，按 1 处理"
            )
            cache["rob_target_daily_limit"] = 1
            if self.config is not None:
                self.config["rob_target_daily_limit"] = 1
                self.config.save_config()
        return cache

    @staticmethod
    def _sanitize_lottery_tiers(val) -> str:
        """校验 lottery_tiers 配置，非法时回退默认档位并告警（防 WebUI 手改坏 JSON）。"""
        from .config.defaults import DEFAULT_CONFIG, validate_and_cast

        try:
            return validate_and_cast("lottery_tiers", str(val))
        except ValueError:
            logger.warning(
                f"Invalid lottery_tiers config, falling back to default: {val!r}"
            )
            return DEFAULT_CONFIG["lottery_tiers"]

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
        """移除已注册的定时任务（配置热更新与终止时复用）。

        add_basic_job 返回的是 CronJob 数据对象（无 remove() 方法），必须经
        cron_manager.delete_job(job_id) 才能真正从调度器与数据库中移除，
        否则禁用/热更新会反复累积重复任务。
        """
        cron_mgr = getattr(self.context, "cron_manager", None)
        for attr in ("_backup_job", "_birthday_job"):
            job = getattr(self, attr, None)
            if not job:
                continue
            job_id = getattr(job, "job_id", None)
            if job_id and cron_mgr and hasattr(cron_mgr, "delete_job"):
                try:
                    await cron_mgr.delete_job(job_id)
                    continue
                except Exception:
                    pass
            try:
                job.remove()
            except Exception:
                pass
        self._backup_job = None
        self._birthday_job = None

    async def _cleanup_stale_cron_jobs(self) -> None:
        """幂等清理本插件历史遗留的非持久化 cron 任务行。

        旧版本 `_remove_cron_jobs` 调用了不存在的 `job.remove()`（异常被静默
        吞掉），禁用/热更新插件时任务从未真正移除，导致 cron_jobs 表行与调度
        器任务反复累积。残留行 persistent=0，重启后不会被重新调度，但会在每次
        启用时生成新行；此方法通过 cron manager 的 API 逐条删除，自愈历史数据。
        """
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None or not hasattr(cron_mgr, "list_jobs"):
            return
        try:
            jobs = await cron_mgr.list_jobs()
        except Exception as e:
            logger.warning(f"Failed to list cron jobs for cleanup: {e}")
            return
        stale = [
            job
            for job in jobs
            if not getattr(job, "persistent", True)
            and getattr(job, "job_type", None) == "basic"
            and getattr(job, "name", None)
            in ("points_backup", "birthday_announce")
        ]
        for job in stale:
            try:
                await cron_mgr.delete_job(job.job_id)
            except Exception as e:
                logger.warning(f"Failed to remove stale cron job {job.job_id}: {e}")

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
                    chain = MessageChain().message(
                        "\U0001f382 \u751f\u65e5\u795d\u798f\uff01\u4eca\u5929\u8fc7\u751f\u65e5\u7684\u670b\u53cb\u6709\uff1a"
                    )
                    for u in users:
                        chain.at(str(u), u)
                    chain.message(
                        "\n\u795d\u5927\u5bb6\u751f\u65e5\u5feb\u4e50\uff01"
                    )
                    if await self._send_birthday_announce(gid, chain):
                        await self.dao.mark_birthday_announced(
                            gid, today, json.dumps(users)
                        )
                    else:
                        logger.warning(
                            f"Failed to send birthday msg to {gid}: no matching platform instance"
                        )
            except Exception as e:
                logger.error(f"Birthday announce error for group {gid}: {e}")

    async def _send_birthday_announce(self, group_id: str, chain) -> bool:
        """生日报播主动发送：遍历平台实例取机器人名称（实例 id）构造 origin，首个成功即返回。

        context.send_message 仅当 session 首段 == platform.meta().id 时才找到平台实例，
        平台类型名（如 aiocqhttp）无法匹配，此前会导致播报静默失败。
        """
        platform_manager = getattr(
            getattr(self.context, "platform_manager", None), "platform_insts", None
        )
        if not platform_manager:
            return False
        for inst in platform_manager:
            pid = getattr(inst.meta(), "id", None)
            if not pid:
                continue
            origin = f"{pid}:{MessageType.GROUP_MESSAGE.value}:{group_id}"
            try:
                if await self.context.send_message(origin, chain):
                    return True
            except Exception as e:
                logger.warning(
                    f"Birthday send to {group_id} via {pid} failed: {e}"
                )
        return False

    # ═══════════════════════════════════════════════════════════
    # Handlers: Sign-in
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"(?:\u7b7e\u5230|\u6253\u5361|(?i:\bsign\b))")
    async def on_sign_in(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        # 严格匹配触发（v0.2.1）：消息必须完全等于签到关键词，普通聊天含"签到"字样不拦截
        if not is_signin_message(
            event.get_message_str(), self.config_cache.get("keyword_sign", [])
        ):
            return
        produced = False
        async for result in self.sign_in_handler.handle(event):
            produced = True
            yield result
        if produced:
            event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Lottery
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"(?:\u62bd\u5956|(?i:\blottery\b))")
    async def on_lottery(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        # 严格匹配触发：消息等于 抽奖关键词 / 口令+关键词 / 关键词+口令
        msg = event.get_message_str()
        cfg = self.config_cache
        if not is_lottery_message(
            msg,
            cfg.get("lottery_passphrase", ""),
            cfg.get("keyword_lottery", []),
        ):
            return
        produced = False
        async for result in self.lottery_handler.handle(event):
            produced = True
            yield result
        if produced:
            event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Ranking
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"\u6392\u884c|\u6392\u540d|\u79ef\u5206\u699c")
    async def on_ranking(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        # 严格匹配触发：消息必须完全等于 排行/排名/积分榜
        if not is_ranking_message(event.get_message_str()):
            return
        produced = False
        async for result in self.ranking_handler.handle(event):
            produced = True
            yield result
        if produced:
            event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: My Points
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"\u6211\u7684\u79ef\u5206|\u79ef\u5206\u67e5\u8be2")
    async def on_my_points(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        # 严格匹配触发：消息必须完全等于 我的积分/积分查询
        if not is_my_points_message(event.get_message_str()):
            return
        produced = False
        async for result in self.my_points_handler.handle(event):
            produced = True
            yield result
        if produced:
            event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Rob
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"\u6253\u52ab")
    async def on_rob(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        # 粗筛后由 handler 严格解析（@ 目标 + 关键词形态），不命中不产出不拦截
        produced = False
        async for result in self.rob_handler.handle(event):
            produced = True
            yield result
        if produced:
            event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Redeem (command)
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u5546\u54c1\u5151\u6362", alias={"\u5151\u6362"})
    async def cmd_redeem(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
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
        # 中英别名归一化（全部→all / 未核销→pending，英文保留）
        if target is not None:
            from .handlers.admin import _RECORD_FILTER_ALIASES

            target = _RECORD_FILTER_ALIASES.get(target, target)
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
        parts = msg.split(maxsplit=3)
        if len(parts) < 2:
            yield event.plain_result(
                "\u7528\u6cd5: /\u6838\u9500 [\u901a\u8fc7|\u9a73\u56de] <\u8bb0\u5f55\u7f16\u53f7> [\u5907\u6ce8]"
            )
            return
        action = "verified"
        record_no = parts[1]
        note = parts[2] if len(parts) >= 3 else ""
        p1 = parts[1].lower()
        if p1 in ("\u901a\u8fc7", "pass"):
            action = "verified"
            if len(parts) < 3:
                yield event.plain_result(
                    "\u7528\u6cd5: /\u6838\u9500 [\u901a\u8fc7|\u9a73\u56de] <\u8bb0\u5f55\u7f16\u53f7> [\u5907\u6ce8]"
                )
                return
            record_no = parts[2]
            note = parts[3] if len(parts) >= 4 else ""
        elif p1 in ("\u9a73\u56de", "reject"):
            action = "rejected"
            if len(parts) < 3:
                yield event.plain_result(
                    "\u7528\u6cd5: /\u6838\u9500 [\u901a\u8fc7|\u9a73\u56de] <\u8bb0\u5f55\u7f16\u53f7> [\u5907\u6ce8]"
                )
                return
            record_no = parts[2]
            note = parts[3] if len(parts) >= 4 else ""
        async for r in self.redeem_handler.verify_record(event, record_no, action, note):
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
            # 中英别名归一化（全部→all，英文保留）
            from .handlers.admin import _RECORD_FILTER_ALIASES

            p1 = _RECORD_FILTER_ALIASES.get(parts[1], parts[1])
            if p1 == "all":
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
                        "\u53c2\u6570\u9519\u8bef: \u7528\u6cd5 / \u6d41\u6c34 [\u9875\u7801 | all/\u5168\u90e8 | @\u7528\u6237] [\u9875\u7801]"
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
            self._cache_sweep_task.cancel()
            await asyncio.gather(self._cache_sweep_task, return_exceptions=True)
        await self._remove_cron_jobs()
        if hasattr(self, "db"):
            await self.db.close()
        logger.info("Point system plugin terminated.")
