import json
from astrbot.api.event import filter, AstrMessageEvent, EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api.message_chain import MessageChain
from astrbot.api import logger

from db.connection import DatabaseManager
from db.schema import init_schema
from db.dao import PointDAO
from config.defaults import DEFAULT_CONFIG
from utils.rate_limiter import RateLimiter


@register("points_system", "WHLofficial",
          "\u79ef\u5206\u7cfb\u7edf\u63d2\u4ef6\uff1a\u7b7e\u5230/\u62bd\u5956/\u5151\u6362/\u6392\u884c/\u751f\u65e5\u7b49", "1.0.0")
class PointSystemPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        self.db = DatabaseManager()
        await self.db.init()

        self.dao = PointDAO(self.db)

        await init_schema(self.db)

        self.config_cache = await self._load_config_cache()
        self.rate_limiter = RateLimiter()

        from services.point_service import PointService
        from services.easter_service import EasterService
        from services.date_reward_service import DateRewardService

        self.point_service = PointService(self.db, self.dao)
        self.easter_service = EasterService(self.dao)
        self.date_reward_service = DateRewardService(self.dao)

        from services.sign_in_service import SignInService
        from services.lottery_service import LotteryService
        from services.redeem_service import RedeemService
        from services.ranking_service import RankingService
        from services.daily_keyword_service import DailyKeywordService
        from services.birthday_service import BirthdayService
        from services.backup_service import BackupService

        self.sign_in_service = SignInService(
            self.db, self.dao, self.point_service,
            self.easter_service, self.date_reward_service, self.config_cache,
        )
        self.lottery_service = LotteryService(self.db, self.dao, self.point_service, self.config_cache)
        self.redeem_service = RedeemService(self.db, self.dao, self.point_service)
        self.ranking_service = RankingService(self.dao)
        self.daily_keyword_service = DailyKeywordService(self.db, self.dao, self.point_service)
        self.birthday_service = BirthdayService(self.dao)
        self.backup_service = BackupService(self.db, self.dao)

        from handlers.sign_in import SignInHandler
        from handlers.lottery import LotteryHandler
        from handlers.redeem import RedeemHandler
        from handlers.ranking import RankingHandler
        from handlers.admin import AdminHandler
        from handlers.birthday import BirthdayHandler
        from handlers.active_reward import ActiveRewardHandler

        self.sign_in_handler = SignInHandler(self)
        self.lottery_handler = LotteryHandler(self)
        self.redeem_handler = RedeemHandler(self)
        self.ranking_handler = RankingHandler(self)
        self.admin_handler = AdminHandler(self)
        self.birthday_handler = BirthdayHandler(self)
        self.active_reward_handler = ActiveRewardHandler(self)

        await self._start_cron_jobs()

        logger.info("Point system plugin initialized.")

    async def _load_config_cache(self) -> dict:
        cache = dict(DEFAULT_CONFIG)
        rows = await self.dao.get_all_config()
        for r in rows:
            key = r["key"]
            if key in ("schema_version",):
                continue
            val = r["value"]
            default = DEFAULT_CONFIG.get(key)
            if isinstance(default, bool):
                cache[key] = val.lower() in ("true", "1", "yes")
            elif isinstance(default, int):
                cache[key] = int(val)
            elif isinstance(default, float):
                cache[key] = float(val)
            else:
                cache[key] = val
        return cache

    async def _start_cron_jobs(self):
        try:
            cfg = self.config_cache

            if cfg.get("backup_enabled", True):
                job = await self.context.cron_manager.add_basic_job(
                    name="points_backup",
                    cron_expression="0 3 * * *",
                    handler=self._cron_backup,
                    description="Daily point system backup",
                    timezone="Asia/Shanghai",
                )
                self._backup_job = job
                logger.info("Backup cron job scheduled at 03:00 daily.")

            announce_time = cfg.get("birthday_announce_time", "08:00")
            hour, minute = announce_time.split(":")
            cron_expr = f"{minute} {hour} * * *"
            job2 = await self.context.cron_manager.add_basic_job(
                name="birthday_announce",
                cron_expression=cron_expr,
                handler=self._cron_birthday_announce,
                description="Daily birthday announcement",
                timezone="Asia/Shanghai",
            )
            self._birthday_job = job2
            logger.info(f"Birthday announce cron job scheduled at {announce_time}.")
        except Exception as e:
            logger.warning(f"Failed to schedule cron jobs: {e}")
            self._backup_job = None
            self._birthday_job = None

    async def _cron_backup(self):
        logger.info("Running scheduled backup...")
        await self.backup_service.run_backup()

    async def _cron_birthday_announce(self):
        logger.info("Running birthday announcement...")
        group_ids = await self.dao.get_all_group_ids()
        for gid in group_ids:
            try:
                result = await self.birthday_service.announce_birthdays(gid)
                if result.get("announced"):
                    users = result["users"]
                    at_str = " ".join(f"@{u}" for u in users)
                    platform = "aiocqhttp"
                    row = await self.dao.get_user(users[0], gid)
                    if row and row["platform"]:
                        platform = row["platform"]
                    origin = f"{platform}:group:{gid}"
                    try:
                        msg_text = f"\U0001f382 \u751f\u65e5\u795d\u798f\uff01\u4eca\u5929\u8fc7\u751f\u65e5\u7684\u670b\u53cb\u6709\uff1a{at_str}\n\u795d\u5927\u5bb6\u751f\u65e5\u5feb\u4e50\uff01"
                        await self.context.send_message(
                            origin,
                            MessageChain().message(msg_text),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send birthday msg to {gid}: {e}")
            except Exception as e:
                logger.error(f"Birthday announce error for group {gid}: {e}")

    # ═══════════════════════════════════════════════════════════
    # Handlers: Sign-in
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"\u7b7e\u5230|sign|\u6253\u5361")
    async def on_sign_in(self, event: AstrMessageEvent):
        async for result in self.sign_in_handler.handle(event):
            yield result
        event.stop_event()

    # ═══════════════════════════════════════════════════════════
    # Handlers: Lottery
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"\u62bd\u5956|lottery")
    async def on_lottery(self, event: AstrMessageEvent):
        async for result in self.lottery_handler.handle(event):
            yield result

    # ═══════════════════════════════════════════════════════════
    # Handlers: Ranking
    # ═══════════════════════════════════════════════════════════

    @filter.regex(r"\u6392\u884c|\u6392\u540d|\u79ef\u5206\u699c")
    async def on_ranking(self, event: AstrMessageEvent):
        async for result in self.ranking_handler.handle(event):
            yield result

    # ═══════════════════════════════════════════════════════════
    # Handlers: Redeem (command)
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u5151\u6362")
    async def cmd_redeem(self, event: AstrMessageEvent):
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
    async def cmd_redeem_records(self, event: AstrMessageEvent):
        msg = event.get_message_str()
        parts = msg.split(maxsplit=2)
        target = parts[1] if len(parts) >= 2 else None
        page = parts[2] if len(parts) >= 3 else "1"
        async for r in self.redeem_handler.list_records(event, target, page):
            yield r

    @filter.command("\u6838\u9500")
    async def cmd_verify(self, event: AstrMessageEvent):
        msg = event.get_message_str()
        parts = msg.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("\u7528\u6cd5: /\u6838\u9500 <\u8bb0\u5f55\u7f16\u53f7> [\u5907\u6ce8]")
            return
        async for r in self.redeem_handler.toggle_verify(event, parts[1]):
            yield r

    @filter.command("\u6dfb\u52a0\u5151\u6362")
    async def cmd_add_item(self, event: AstrMessageEvent):
        async for r in self.admin_handler.add_item(event):
            yield r

    @filter.command("\u5220\u9664\u5151\u6362")
    async def cmd_delete_item(self, event: AstrMessageEvent):
        async for r in self.admin_handler.delete_item(event):
            yield r

    @filter.command("\u4fee\u6539\u5151\u6362")
    async def cmd_modify_item(self, event: AstrMessageEvent):
        async for r in self.admin_handler.modify_item(event):
            yield r

    # ═══════════════════════════════════════════════════════════
    # Handlers: Admin
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u52a0\u5206")
    async def cmd_add_points(self, event: AstrMessageEvent):
        async for r in self.admin_handler.adjust_points(event, "\u52a0\u5206"):
            yield r

    @filter.command("\u6263\u5206")
    async def cmd_sub_points(self, event: AstrMessageEvent):
        async for r in self.admin_handler.adjust_points(event, "\u6263\u5206"):
            yield r

    @filter.command("\u8bbe\u7f6e\u4eca\u65e5\u53e3\u4ee4")
    async def cmd_set_daily_kw(self, event: AstrMessageEvent):
        async for r in self.admin_handler.set_daily_kw(event):
            yield r

    @filter.command("\u6e05\u9664\u4eca\u65e5\u53e3\u4ee4")
    async def cmd_clear_daily_kw(self, event: AstrMessageEvent):
        async for r in self.admin_handler.clear_daily_kw(event):
            yield r

    @filter.command("\u8bbe\u7f6e")
    async def cmd_set_config(self, event: AstrMessageEvent):
        async for r in self.admin_handler.set_config(event):
            yield r

    @filter.command("\u67e5\u770b\u914d\u7f6e")
    async def cmd_view_config(self, event: AstrMessageEvent):
        async for r in self.admin_handler.view_config(event):
            yield r

    @filter.command("\u8bbe\u7f6e\u6298\u6263")
    async def cmd_set_discount(self, event: AstrMessageEvent):
        async for r in self.admin_handler.set_discount(event):
            yield r

    @filter.command("\u6e05\u9664\u6298\u6263")
    async def cmd_clear_discount(self, event: AstrMessageEvent):
        async for r in self.admin_handler.clear_discount(event):
            yield r

    # ═══════════════════════════════════════════════════════════
    # Handlers: Birthday
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u8bbe\u7f6e\u751f\u65e5")
    async def cmd_set_birthday(self, event: AstrMessageEvent):
        async for r in self.birthday_handler.set_birthday(event):
            yield r

    @filter.command("\u67e5\u751f\u65e5")
    async def cmd_query_birthday(self, event: AstrMessageEvent):
        async for r in self.birthday_handler.query_birthday(event):
            yield r

    # ═══════════════════════════════════════════════════════════
    # Handlers: Stats
    # ═══════════════════════════════════════════════════════════

    @filter.command("\u7b7e\u5230\u7edf\u8ba1")
    async def cmd_signin_stats(self, event: AstrMessageEvent):
        async for r in self.ranking_handler.stats(event):
            yield r

    @filter.command("\u6d41\u6c34")
    async def cmd_transactions(self, event: AstrMessageEvent):
        qq = event.get_sender_id()
        group_id = event.get_group_id()
        msg = event.get_message_str()
        parts = msg.split(maxsplit=2)
        target_qq = qq
        page = 1
        if len(parts) >= 2:
            if parts[1].lstrip("@").isdigit():
                from utils.security import parse_qq
                if event.is_admin() or await self.dao.is_admin(qq, group_id):
                    target_qq = parse_qq(parts[1])
                else:
                    yield event.plain_result("\u4f60\u6ca1\u6709\u6743\u9650\u67e5\u770b\u4ed6\u4eba\u6d41\u6c34")
                    return
            elif parts[1] == "all" and (event.is_admin() or await self.dao.is_admin(qq, group_id)):
                target_qq = None
            if len(parts) >= 3 and parts[2].isdigit():
                page = int(parts[2])

        offset = (page - 1) * 10
        records = await self.dao.get_transactions(qq=target_qq, group_id=group_id, limit=10, offset=offset)
        if not records:
            yield event.plain_result("\u6ca1\u6709\u6d41\u6c34\u8bb0\u5f55")
            return
        lines = [f"\U0001f4ca \u79ef\u5206\u6d41\u6c34 (\u7b2c{page}\u9875)"]
        for r in records:
            icon = "\U0001f7e2" if r["amount"] >= 0 else "\U0001f534"
            lines.append(f"{icon} {r['amount']:+d}  {r['reason']}  {r['created_at'][:16]}")
        yield event.plain_result("\n".join(lines))

    # ═══════════════════════════════════════════════════════════
    # Handlers: Active reward (intercepts all group messages)
    # ═══════════════════════════════════════════════════════════

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        await self.active_reward_handler.handle(event)

    # ═══════════════════════════════════════════════════════════
    # Teardown
    # ═══════════════════════════════════════════════════════════

    async def terminate(self):
        if hasattr(self, "_backup_job") and self._backup_job:
            try:
                self._backup_job.remove()
            except Exception:
                pass
        if hasattr(self, "_birthday_job") and self._birthday_job:
            try:
                self._birthday_job.remove()
            except Exception:
                pass
        if hasattr(self, "db"):
            await self.db.close()
        logger.info("Point system plugin terminated.")
